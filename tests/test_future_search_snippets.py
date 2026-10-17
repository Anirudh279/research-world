"""Provider discovery events must never become frozen source evidence."""
import json
from web_research_sim.core import Store
from web_research_sim.bundles import export_snapshot, import_snapshot
from web_research_sim.sessions import SessionManager


def test_future_and_uncaptured_serp_snippets_stay_out_of_replay_and_bundle(tmp_path):
    time=['2026-01-01T10:00:00+00:00']
    store=Store(tmp_path/'source',clock=lambda:time[0])
    wid=store.create_world('What was announced by January 1?')['id']
    url='https://fixture.example/announcement'
    doc=store.add_capture(wid,url=url,body=b'Review remains pending.',text='Review remains pending.',captured_at=time[0])
    # Even an early discovery snippet is not captured source evidence.
    store.event(wid,'search_results',{'query':'announcement','provider':'fixture','results':[
        {'url':'https://fixture.example/uncaptured','snippet':'UNCAPTURED_RESULT_472901'}]})
    sid=store.freeze(wid)['id']
    before=store.replay(sid).search('review')
    time[0]='2026-01-03T10:00:00+00:00'
    future='FUTURE_RESULT_763092'
    store.event(wid,'search_results',{'query':'announcement','provider':'fixture','results':[
        {'url':url,'snippet':future,'published_at':'2020-01-01'}]})
    store.add_capture(wid,url=url,body=future.encode(),text=future,captured_at=time[0],headers={'last-modified':'Wed, 01 Jan 2020 00:00:00 GMT'})
    bundle=export_snapshot(store,sid,redistribution_acknowledged=True)
    target=Store(tmp_path/'recipient');import_snapshot(target,bundle)
    for workspace in (Store(tmp_path/'source'),target):
        replay=workspace.replay(sid)
        assert replay.search('review')==before
        assert replay.search(future)==[]
        assert replay.search('UNCAPTURED_RESULT_472901')==[]
        assert replay.open(doc['id'])['text']=='Review remains pending.'
        sessions=SessionManager(workspace)
        session=sessions.create(sid,provider='fixture',model='fixture',request_id='snippet-boundary')
        result=sessions.tool(session['id'],'search-future','search',query=future)
        assert result['ok'] and result['result']['results']==[]
        result=sessions.tool(session['id'],'open-old','open',document_id=doc['id'])
        assert future not in json.dumps(result)
    assert future.encode() not in bundle
    assert b'UNCAPTURED_RESULT_472901' not in bundle
