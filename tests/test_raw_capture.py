import socket
import pytest
from web_research_sim import raw_capture as raw
from web_research_sim.capture import CaptureError


def fixture(monkeypatch,body,status=200,headers=None):
    seen=[]
    class Response:
        def getheaders(self):return list((headers or {'Content-Type':'application/javascript'}).items())
        def read(self,n):return body[:n]
    Response.status=status
    class Connection:
        def __init__(self,*args):self.args=args
        def request(self,method,target,headers):seen.append((self.args,method,target,headers))
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(raw,'_PinnedConnection',Connection)
    monkeypatch.setattr(raw,'_public_addresses',lambda *a:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    return seen


def test_raw_asset_preserves_bytes_without_extracting_or_auth(monkeypatch):
    body=b'window.answer = 42;';seen=fixture(monkeypatch,body,headers={'Content-Type':'application/javascript','Set-Cookie':'SECRET','Content-Length':str(len(body))})
    r=raw.fetch_raw_response('https://example.org/asset.js?version=1')
    assert r.body==body and r.headers=={'content-type':'application/javascript'}
    assert seen[0][1:3]==('GET','/asset.js?version=1')
    assert set(seen[0][3])=={'User-Agent','Accept-Encoding','Accept'}


def test_redirect_returned_without_following(monkeypatch):
    seen=fixture(monkeypatch,b'redirect',302,{'Location':'http://127.0.0.1/secret'})
    r=raw.fetch_raw_response('https://example.org/')
    assert r.status==302 and r.headers['location'].startswith('http://127.')
    assert len(seen)==1


def test_private_destination_refused_before_socket(monkeypatch):
    called=[]
    def private(*args):raise CaptureError('private_destination')
    monkeypatch.setattr(raw,'_public_addresses',private)
    monkeypatch.setattr(raw,'_PinnedConnection',lambda *a:called.append(1))
    with pytest.raises(CaptureError,match='private_destination'):raw.fetch_raw_response('https://example.org')
    assert called==[]


@pytest.mark.parametrize('body,headers,limit,error',[(b'12345',{'Content-Type':'text/plain'},4,'response_too_large'),(b'123',{'Content-Length':'8'},100,'incomplete_body')])
def test_incomplete_or_oversize_not_accepted(monkeypatch,body,headers,limit,error):
    fixture(monkeypatch,body,headers=headers)
    with pytest.raises(CaptureError,match=error):raw.fetch_raw_response('https://example.org',limit)
