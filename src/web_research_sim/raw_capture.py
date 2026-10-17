"""One public, IP-pinned HTTP response for isolated browser controllers.

No redirects, extraction, cookies, credentials or retries. Caller must preserve
returned originals before browser fulfillment and enforce an outer wall deadline
around this function (DNS and incremental reads are not globally time bounded).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import http.client
from .capture import CaptureError, _parse_url, _public_addresses, _PinnedConnection


@dataclass
class RawResponse:
    url: str
    requested_url: str
    body: bytes
    status: int
    headers: dict
    captured_at: str


def fetch_raw_response(url, max_bytes=2*1024**2, *, timeout_seconds=10):
    if type(max_bytes) is not int or not 1 <= max_bytes <= 2*1024**2:
        raise CaptureError('invalid_raw_byte_limit')
    if type(timeout_seconds) not in (int,float) or not 0 < timeout_seconds <= 20:
        raise CaptureError('invalid_raw_timeout')
    p, host, port = _parse_url(url)
    try:
        addresses = _public_addresses(host, port)
        conn = _PinnedConnection(host, port, addresses[0], p.scheme=='https')
        conn.timeout = timeout_seconds
        try:
            target = p.path or '/'
            if p.query: target += '?' + p.query
            conn.request('GET',target,headers={'User-Agent':'web-research-sim/0.1',
                'Accept-Encoding':'identity','Accept':'*/*'})
            response = conn.getresponse()
            pairs = response.getheaders()
            if sum(len(k)+len(v) for k,v in pairs)>65536:
                raise CaptureError('raw_header_limit')
            headers = {k.lower():v for k,v in pairs}
            # A redirect remains an ordinary preserved response. A browser's
            # subsequent URL must independently pass this same pinned transport.
            body = response.read(max_bytes+1)
            if len(body)>max_bytes: raise CaptureError('response_too_large')
            if headers.get('content-length') and int(headers['content-length'])!=len(body):
                raise CaptureError('incomplete_body')
            safe_headers = {k:v for k,v in headers.items() if k in {
                'content-type','location','content-encoding','content-security-policy',
                'x-content-type-options','etag','last-modified'}}
            return RawResponse(url,url,body,response.status,safe_headers,datetime.now(timezone.utc).isoformat())
        finally:
            conn.close()
    except CaptureError:
        raise
    except (OSError,http.client.HTTPException,ValueError):
        raise CaptureError('raw_capture_transport_or_protocol_error') from None
