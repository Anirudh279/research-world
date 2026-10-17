"""Bounded static capture. Connections pin validated IPs; TLS verifies source host."""
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import subprocess
import sys
import json
from importlib.metadata import version
from urllib.parse import urlsplit, urljoin, urldefrag


class CaptureError(RuntimeError):
    pass


def capture_diagnostic(exc):
    """Stable public failure codes without raw exception or response content."""
    codes = {
        'private_destination': 'The destination is not a permitted public address.',
        'invalid_url': 'The source URL is invalid.',
        'nonstandard_port': 'Only HTTP and HTTPS default ports are supported.',
        'dns_empty': 'The source hostname returned no addresses.',
        'dns_failure': 'The source hostname could not be resolved.',
        'capture_timeout': 'The source request timed out.',
        'tls_failure': 'The source TLS connection could not be verified.',
        'response_too_large': 'The response exceeds the capture byte limit.',
        'incomplete_body': 'The source response was incomplete.',
        'redirect_without_location': 'The source redirect has no destination.',
        'too_many_redirects': 'The source exceeded the redirect limit.',
        'capture_transport_or_protocol_error': 'The source connection or HTTP response failed.',
    }
    code = str(exc) if isinstance(exc, CaptureError) else ''
    if code not in codes:
        code = 'capture_failed'
    return {'error_type': type(exc).__name__, 'error_code': code,
            'message': codes.get(code, 'Capture or persistence failed; inspect the saved operation before retrying.')}


@dataclass
class CapturedPage:
    url: str
    requested_url: str
    body: bytes
    text: str
    title: str
    status: int
    headers: dict
    captured_at: str
    links: list[str]
    extractor: str = "static-text-v2"
    extraction_status: str = "completed"
    extraction_error: str | None = None
    original_response_id: str | None = None


def _public_addresses(host, port):
    if host.lower().rstrip('.') in ('localhost', 'metadata.google.internal') or '%' in host:
        raise CaptureError('private_destination')
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise CaptureError('dns_empty')
    for _, _, _, _, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        # Reject transition/mapped ranges as well as local, reserved, multicast.
        if not ip.is_global or ip.is_multicast or getattr(ip, 'ipv4_mapped', None) or getattr(ip, 'sixtofour', None) or getattr(ip, 'teredo', None):
            raise CaptureError('private_destination')
    return addresses


def _parse_url(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 for c in url):
        raise CaptureError('invalid_url')
    try:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or p.password is not None:
            raise CaptureError('invalid_url')
        port = p.port or (443 if p.scheme == 'https' else 80)
        if port not in (80, 443):
            raise CaptureError('nonstandard_port')
        return p, p.hostname.encode('idna').decode(), port
    except (ValueError, UnicodeError):
        raise CaptureError('invalid_url') from None


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, secure):
        super().__init__(host, port, timeout=20)
        self.address, self.secure = address, secure

    def connect(self):
        family, socktype, proto, _, sockaddr = self.address
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(sockaddr)  # Exact approved IP, no second DNS lookup.
            if self.secure:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title_parts, self.hrefs = [], [], []
        self.hidden = 0
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'a' and not self.hidden:
            href = dict(attrs).get('href')
            if href:
                self.hrefs.append(href)
        if tag in ('td', 'th') and not self.hidden:
            self.parts.append(' | ')
        if tag in ('p', 'div', 'br', 'tr', 'table', 'caption', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'tr' and not self.hidden:
            self.parts.append(' | ')
        if tag in ('p', 'div', 'tr', 'table', 'caption', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)
            if self.in_title:
                self.title_parts.append(data)


def _extract(body, content_type, url):
    mime = content_type.split(';')[0].strip().lower()
    if mime == 'application/pdf':
        try:
            result = subprocess.run([sys.executable, '-m', 'web_research_sim.pdf_worker'], input=body,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
        except subprocess.TimeoutExpired:
            raise CaptureError('pdf_extraction_timeout') from None
        if result.returncode != 0:
            raise CaptureError('pdf_extraction_failed_or_no_text')
        data = json.loads(result.stdout)
        return data['text'], data['title'], []
    if mime not in ('text/html', 'application/xhtml+xml', 'text/plain', 'text/csv', 'application/json') and not mime.endswith('+json'):
        raise CaptureError('unsupported_content_type:' + mime)
    charset = re.search(r'charset\s*=\s*["\']?([^;\s"\']+)', content_type, re.I)
    try:
        decoded = body.decode(charset.group(1) if charset else 'utf-8', errors='replace')
    except LookupError:
        raise CaptureError('unsupported_charset') from None
    if mime not in ('text/html', 'application/xhtml+xml'):
        return decoded, '', []
    parser = _Text()
    parser.feed(decoded)
    links = []
    for href in parser.hrefs:
        target = urldefrag(urljoin(url, href))[0]
        try:
            _parse_url(target)
        except CaptureError:
            continue
        if target not in links:
            links.append(target)
    text = '\n'.join(' '.join(line.split()) for line in ''.join(parser.parts).splitlines() if line.strip())
    return text, ' '.join(''.join(parser.title_parts).split()), links


def fetch_page(url, max_bytes=2 * 1024**2, *, original_sink=None):
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 16 * 1024**2:
        raise ValueError('max_bytes must be 1..16 MiB')
    requested, current = url, url
    for hop in range(6):
        p, host, port = _parse_url(current)
        try:
            addresses = _public_addresses(host, port)
            conn = _PinnedConnection(host, port, addresses[0], p.scheme == 'https')
            try:
                target = p.path or '/'
                if p.query:
                    target += '?' + p.query
                conn.request('GET', target, headers={'User-Agent': 'web-research-sim/0.1', 'Accept-Encoding': 'identity', 'Accept': 'text/html,text/plain,application/json;q=0.9,*/*;q=0.1'})
                response = conn.getresponse()
                headers = {k.lower(): v for k, v in response.getheaders()}
                if response.status in (301, 302, 303, 307, 308):
                    if not headers.get('location'):
                        raise CaptureError('redirect_without_location')
                    current = urljoin(current, headers['location'])
                    continue  # Revalidate destination and DNS on every redirect.
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise CaptureError('response_too_large')
                # Detect truncated declared bodies even where the transport does not.
                if headers.get('content-length') and int(headers['content-length']) != len(body):
                    raise CaptureError('incomplete_body')
                captured_at = datetime.now(timezone.utc).isoformat()
                content_type = headers.get('content-type', '')
                receipt = original_sink(body, {'url': current, 'requested_url': requested,
                    'status': response.status, 'captured_at': captured_at, 'content_type': content_type}) if original_sink else None
                extractor = 'pypdf-' + version('pypdf') if content_type.split(';')[0].strip().lower() == 'application/pdf' else 'static-text-v2'
                extraction_status, extraction_error = 'completed', None
                try:
                    if headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise CaptureError('unsupported_content_encoding')
                    text, title, links = _extract(body, content_type, current)
                    if response.status >= 400:
                        extraction_status, extraction_error = 'partial', 'http_error'
                except CaptureError as exc:
                    # Keep the original response even when it cannot be extracted.
                    text, title, links = '', '', []
                    extraction_status, extraction_error = 'failed', str(exc)[:200]
                return CapturedPage(current, requested, body, text, title, response.status, headers,
                                    captured_at, links, extractor, extraction_status, extraction_error, receipt)
            finally:
                conn.close()
        except CaptureError:
            raise
        except socket.gaierror:
            raise CaptureError('dns_failure') from None
        except (TimeoutError, socket.timeout):
            raise CaptureError('capture_timeout') from None
        except ssl.SSLError:
            raise CaptureError('tls_failure') from None
        except (OSError, http.client.HTTPException, ValueError):
            raise CaptureError('capture_transport_or_protocol_error') from None
    raise CaptureError('too_many_redirects')
