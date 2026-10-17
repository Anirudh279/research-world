import io
import socket
import pytest
from web_research_sim import capture as c


@pytest.mark.parametrize('address', ['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','::ffff:127.0.0.1','224.0.0.1'])
def test_private_dns_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,80))])
    with pytest.raises(c.CaptureError, match='private_destination'):
        c._public_addresses('example.org',80)


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://user:pass@example.org','http://example.org:8080','http://example.org/\r\nx:1'])
def test_url_boundary(url):
    with pytest.raises(c.CaptureError):
        c._parse_url(url)


def test_html_extract_links_and_original():
    body = b'<title>Test</title><h1>Hello</h1><script>bad()</script><p>A &amp; B <a href="/next#x">Next</a></p>'
    text,title,links = c._extract(body,'text/html; charset=utf-8','https://example.org/start')
    assert 'bad' not in text and 'A & B' in text and title == 'Test'
    assert links == ['https://example.org/next']


def test_invalid_pdf_fails_explicitly():
    with pytest.raises(c.CaptureError, match='pdf_extraction_failed'):
        c._extract(b'%PDF', 'application/pdf', 'https://example.org/a.pdf')


def test_socket_pins_resolved_address(monkeypatch):
    events = []
    class Sock:
        def settimeout(self, x): pass
        def connect(self, address): events.append(address)
        def close(self): pass
    monkeypatch.setattr(socket,'socket',lambda *a: Sock())
    connection = c._PinnedConnection('example.org',80,(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',80)),False)
    connection.connect()
    assert events == [('93.184.216.34',80)]


def test_redirect_to_private_blocked(monkeypatch):
    class Response:
        status = 302
        def getheaders(self): return [('location','http://127.0.0.1/secret')]
    class Connection:
        def __init__(self,*a): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Response()
        def close(self): pass
    def addresses(host,port):
        if host == '127.0.0.1': raise c.CaptureError('private_destination')
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',80))]
    monkeypatch.setattr(c,'_public_addresses',addresses)
    monkeypatch.setattr(c,'_PinnedConnection',Connection)
    with pytest.raises(c.CaptureError, match='private_destination'):
        c.fetch_page('http://example.org')


def _response_fixture(monkeypatch, body, *, headers=None, status=200):
    class Response:
        def __init__(self): self.status = status
        def getheaders(self): return list((headers or {'content-type':'text/html'}).items())
        def read(self,n): return body[:n]
    class Connection:
        def __init__(self,*a): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(c,'_public_addresses',lambda *a:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',80))])
    monkeypatch.setattr(c,'_PinnedConnection',Connection)


def test_complete_capture_preserves_body_and_status(monkeypatch):
    body = b'<title>Missing</title><p>Unavailable</p>'
    _response_fixture(monkeypatch,body,status=404)
    result = c.fetch_page('https://example.org/')
    assert result.body == body and result.status == 404
    assert result.requested_url == result.url and result.captured_at.endswith('+00:00')


def test_oversize_not_silently_truncated(monkeypatch):
    _response_fixture(monkeypatch,b'x'*101)
    with pytest.raises(c.CaptureError,match='response_too_large'):
        c.fetch_page('https://example.org/',max_bytes=100)


def test_partial_body_not_accepted(monkeypatch):
    _response_fixture(monkeypatch,b'abc',headers={'content-type':'text/plain','content-length':'10'})
    with pytest.raises(c.CaptureError,match='incomplete_body'):
        c.fetch_page('https://example.org/')


def test_http_error_preserved_with_explicit_gap(monkeypatch):
    body=b'<p>Not found</p>'
    _response_fixture(monkeypatch,body,status=404)
    saved=[]
    page=c.fetch_page('https://example.org/missing',original_sink=lambda b,m:saved.append((b,m)))
    assert saved[0][0]==body and saved[0][1]['status']==404
    assert page.body==body and page.text=='Not found'
    assert page.extraction_status=='partial' and page.extraction_error=='http_error'


@pytest.mark.parametrize('failure,code',[(socket.gaierror('private detail'),'dns_failure'),(TimeoutError('private detail'),'capture_timeout')])
def test_transport_diagnostic_is_fixed(monkeypatch,failure,code):
    def fail(*args):raise failure
    monkeypatch.setattr(c,'_public_addresses',fail)
    with pytest.raises(c.CaptureError) as exc:c.fetch_page('https://example.org')
    assert c.capture_diagnostic(exc.value)['error_code']==code
    assert 'private detail' not in str(c.capture_diagnostic(exc.value))


def test_untrusted_capture_exception_does_not_leak():
    diagnostic=c.capture_diagnostic(c.CaptureError('private_destination: secret-token-content'))
    assert diagnostic['error_code']=='capture_failed'
    assert 'secret-token' not in str(diagnostic)
