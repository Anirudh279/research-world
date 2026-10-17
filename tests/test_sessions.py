import concurrent.futures
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.sessions import SessionManager

T='2026-01-01T00:00:00+00:00'


@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path,clock=lambda:T)
    wid=store.create_world('What is the level?')['id']
    one=store.add_capture(wid,url='https://example.org/a',body=b'a',text='Level is 42 cm. '+'x'*300+' Another observation.',captured_at=T,title='A',links=['https://example.org/b','https://example.org/missing'])['id']
    two=store.add_capture(wid,url='https://example.org/b',body=b'b',text='Independent level is 43 cm.',captured_at=T,title='B')['id']
    snap=store.freeze(wid)['id']; sm=SessionManager(store)
    session=sm.create(snap,provider='native-declared',model='user-said-model',tool_limit=10,request_id='start')['id']
    return store,sm,session,snap,one,two


def test_idempotent_create_and_reset_preserve_old_trace(setup):
    store,sm,sid,snap,one,two=setup
    assert sm.create(snap,provider='native-declared',model='user-said-model',tool_limit=10,request_id='start')['id']==sid
    with pytest.raises(SimError): sm.create(snap,provider='other',model='x',request_id='start')
    sm.tool(sid,'open','open',document_id=one)
    new=sm.reset(sid,request_id='reset')
    assert new['id']!=sid and new['parent_id']==sid and new['tool_calls']==0
    assert sm.reset(sid,request_id='reset')['id']==new['id'] and sm.get(sid)['tool_calls']==1
    run=sm.submit(new['id'],answer='42',citations=[{'document_id':one,'quote':'Level is 42 cm.'}])
    assert run['status']=='invalid_citations'


def test_idempotent_tool_calls_and_restart(setup):
    store,sm,sid,snap,one,two=setup
    before=sm.tool(sid,'key','open',document_id=one)
    restarted=SessionManager(Store(store.root,clock=lambda:T))
    assert restarted.tool(sid,'key','open',document_id=one)==before
    assert restarted.get(sid)['tool_calls']==1
    with pytest.raises(SimError) as exc: restarted.tool(sid,'key','open',document_id=two)
    assert exc.value.code=='operation_conflict'


def test_failed_attempts_count_and_limit_blocks(setup):
    store,sm,sid,snap,one,two=setup
    sid=sm.create(snap,provider='x',model='y',tool_limit=2)['id']
    a=sm.tool(sid,'1','open',document_id='missing')
    b=sm.tool(sid,'2','unknown')
    assert not a['ok'] and not b['ok'] and sm.get(sid)['tool_calls']==2
    with pytest.raises(SimError) as exc: sm.tool(sid,'3','search',query='level')
    assert exc.value.code=='tool_budget_exhausted'
    assert sm.tool(sid,'1','open',document_id='missing')==a


def test_follow_requires_returned_source_and_never_live_fetches(setup):
    store,sm,sid,snap,one,two=setup
    assert sm.tool(sid,'1','follow',document_id=one,url='https://example.org/b')['error']['code']=='source_not_opened'
    sm.tool(sid,'2','open',document_id=one)
    assert sm.tool(sid,'3','follow',document_id=one,url='https://example.org/b')['result']['id']==two
    assert sm.tool(sid,'4','follow',document_id=one,url='https://example.org/missing')['error']['code']=='archive_miss'
    assert sm.tool(sid,'5','follow',document_id=one,url='https://other.example')['error']['code']=='link_not_in_source'


def test_submission_exact_quote_and_declared_identity(setup):
    store,sm,sid,snap,one,two=setup
    sm.tool(sid,'open','open',document_id=one)
    citation={'document_id':one,'quote':'Level is 42 cm.'}
    run=sm.submit(sid,answer='42 cm',citations=[citation])
    assert run['status']=='completed' and run['observed_models']==[] and run['cost_usd'] is None
    assert run['model_identity']=='declared_unverified' and 'tool_scoped' in run['isolation']
    assert store.run(run['id'])==run and sm.submit(sid,answer='42 cm',citations=[citation])==run
    with pytest.raises(SimError) as exc: sm.submit(sid,answer='43 cm',citations=[citation])
    assert exc.value.code=='submission_conflict'
    with pytest.raises(SimError): sm.tool(sid,'new','search',query='level')
    assert sm.tool(sid,'open','open',document_id=one)['ok']


def test_search_snippet_is_not_opened_evidence(setup):
    store,sm,sid,snap,one,two=setup
    sm.tool(sid,'search','search',query='level')
    run=sm.submit(sid,answer='42',citations=[{'document_id':one,'quote':'Level is 42 cm.'},{'document_id':[],'quote':'bad'}])
    assert len(run['citation_errors'])==2


def test_find_contexts_cannot_be_stitched_for_citation(setup):
    store,sm,sid,snap,one,two=setup
    a=sm.tool(sid,'find1','find',document_id=one,query='Level')['result']['matches'][0]['context']
    b=sm.tool(sid,'find2','find',document_id=one,query='Another')['result']['matches'][0]['context']
    run=sm.submit(sid,answer='42',citations=[{'document_id':one,'quote':a+'\n'+b}])
    assert run['status']=='invalid_citations'


def test_concurrent_duplicate_submission_is_one_run(setup):
    store,sm,sid,snap,one,two=setup
    sm.tool(sid,'open','open',document_id=one)
    def submit(_):
        return SessionManager(Store(store.root,clock=lambda:T)).submit(sid,answer='42',citations=[{'document_id':one,'quote':'42 cm'}])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(submit,range(4)))
    assert len({r['id'] for r in results})==1
    with store.db() as db: assert db.execute('SELECT count(*) FROM runs').fetchone()[0]==1


def test_malformed_tool_identifier_is_counted_error(setup):
    store,sm,sid,snap,one,two=setup
    result=sm.tool(sid,'bad','open',document_id=[])
    assert not result['ok'] and sm.get(sid)['tool_calls']==1
