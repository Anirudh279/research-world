import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.sessions import SessionManager


def world(tmp_path):
    store=Store(tmp_path)
    wid=store.create_world('What does the appendix say?')['id']
    text='é🦉'*9000+'Appendix: the measured depth was 42 metres.'
    doc=store.add_capture(wid,url='https://example.org/report',body=text.encode(),text=text,captured_at='2026-01-01T00:00:00Z')
    snap=store.freeze(wid)['id']
    return store,doc,snap,text


def test_unicode_windows_reconstruct_and_restart(tmp_path):
    store,doc,snap,text=world(tmp_path)
    replay=store.replay(snap); parts=[]; offset=0
    while offset is not None:
        page=replay.read(doc['id'],offset,997)
        assert len(page['text'])<=997
        assert Store(tmp_path).replay(snap).read(doc['id'],offset,997)==page
        parts.append(page['text']);offset=page['next_offset']
    assert ''.join(parts)==text
    assert replay.read(doc['id'],len(text))['text']==''
    for offset,size in [(-1,10),(True,10),(0,0),(0,20001),(len(text)+1,10),(0,False)]:
        with pytest.raises(SimError): replay.read(doc['id'],offset,size)


def test_later_quote_requires_reading_later_window(tmp_path):
    store,doc,snap,text=world(tmp_path); sm=SessionManager(store)
    quote='Appendix: the measured depth was 42 metres.'
    first=sm.create(snap,provider='fixture',model='fixture')['id']
    page=sm.tool(first,'page1','open',document_id=doc['id'])['result']
    assert page['next_offset']==16000 and quote not in page['text']
    assert sm.submit(first,answer='42 metres',citations=[{'document_id':doc['id'],'quote':quote}])['status']=='invalid_citations'
    second=sm.create(snap,provider='fixture',model='fixture')['id']
    later=sm.tool(second,'page2','open',document_id=doc['id'],offset=16000)
    assert quote in later['result']['text']
    assert sm.tool(second,'page2','open',document_id=doc['id'],offset=16000)==later
    assert sm.submit(second,answer='42 metres',citations=[{'document_id':doc['id'],'quote':quote}])['status']=='completed'


def test_http_read_window_and_bad_offset(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from web_research_sim.api import create_app
    monkeypatch.delenv('WR_SIM_TOKEN',raising=False)
    store,doc,snap,text=world(tmp_path)
    with TestClient(create_app(tmp_path),base_url='http://localhost') as client:
        path=f'/api/snapshots/{snap}/documents/{doc["id"]}/read'
        response=client.get(path,params={'offset':18000,'max_chars':100})
        assert response.status_code==200
        assert response.json()['text']==text[18000:18100]
        assert response.json()['next_offset'] is None
        assert client.get(path,params={'offset':-1}).status_code==400
