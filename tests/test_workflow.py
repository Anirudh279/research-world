"""Deterministic fixture integration checks; not evidence of live provider access."""
import json
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.capture import CapturedPage
from web_research_sim.providers import ModelReply, SearchReply, ProviderError
from web_research_sim.workflow import Workflow, compare

TIME = '2026-01-01T12:00:00+00:00'
URL = 'https://example.org/evidence'


class ScriptedLLM:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []
        self.closed = False
    def complete(self, messages):
        self.calls.append(json.loads(json.dumps(messages)))
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            action = action(messages)
        return ModelReply(action, {'fixture': action}, 'fixture-served-model', {'input_tokens': 1})
    def close(self): self.closed = True


class ScriptedSearch:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{'url':URL,'title':'Evidence','snippet':'water level'}]
        self.calls = []
        self.closed = False
    def search(self, query, limit=5):
        self.calls.append((query,limit))
        return SearchReply(self.rows, {'fixture_results':self.rows}, 'fixture-search')
    def close(self): self.closed = True


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path,clock=lambda:TIME)
    world = store.create_world('What is the water level?', {'model_calls':20,'search_calls':3,'capture_calls':3})
    return store,world['id']


def page(url):
    return CapturedPage(url,url,b'<p>Water level is 42 cm.</p>','Water level is 42 cm.','Evidence',200,{'content-type':'text/html'},TIME,[])


def workflow(store, actions, search=None, fetch=page):
    llm, search = ScriptedLLM(actions), search or ScriptedSearch()
    flow = Workflow(store,cost_admission=None,llm_factory=lambda provider,model:llm,search_factory=lambda provider:search,fetch=fetch)
    return flow,llm,search


def collect(flow, wid, job='job'):
    return flow.collect(wid,job,model_provider='fixture',model='fixture-choice',search_provider='fixture',max_queries=2,max_pages=2)


def frozen(state):
    store,wid = state
    doc = store.add_capture(wid,**page(URL).__dict__)
    return store,wid,doc['id'],store.freeze(wid)['id']


def research(flow, snapshot, job='research', max_steps=8):
    return flow.research(snapshot,job,model_provider='fixture',model='fixture-choice',max_steps=max_steps)


def test_collection_persists_raw_and_reservations_before_network(state):
    store,wid = state
    fetched = []
    def fetch(url):
        assert store.usage(wid)['capture_calls']['reserved'] == 1
        fetched.append(url)
        return page(url)
    flow,llm,search = workflow(store,[{'queries':['water level','water level'],'directions':['direct','counter']}, {'selected_urls':[URL,URL]}],fetch=fetch)
    result = collect(flow,wid)
    assert result['captured'] == 1 and result['failed'] == 0
    assert search.calls == [('water level',10)] and fetched == [URL]
    assert llm.closed and search.closed
    usage = store.usage(wid)
    assert {k:v['reserved'] for k,v in usage.items()} == {'model_calls':2,'search_calls':1,'capture_calls':1}
    doc = store.captures(wid)[0]
    assert store.objects.get(doc['body_sha256']) == page(URL).body
    with store.db() as db:
        rows = db.execute('SELECT status,result FROM operations').fetchall()
    assert all(r['status']=='completed' for r in rows)
    provider_results = [json.loads(r['result']) for r in rows if 'parsed_provider_response_sha256' in r['result']]
    assert len(provider_results) == 3
    assert all(json.loads(store.objects.get(r['parsed_provider_response_sha256'])) for r in provider_results)


def test_consumed_job_does_not_call_provider_again(state):
    store,wid = state
    flow,llm,_ = workflow(store,[{'queries':['water']},{'selected_urls':[URL]}])
    collect(flow,wid)
    with pytest.raises(SimError) as caught:
        collect(flow,wid)
    assert caught.value.code == 'operation_consumed' and len(llm.calls) == 2


def test_budget_prevents_next_model_call(state):
    store,_ = state
    wid = store.create_world('Question',{'model_calls':1,'search_calls':1,'capture_calls':1})['id']
    flow,llm,search = workflow(store,[{'queries':['water']},{'selected_urls':[URL]}])
    with pytest.raises(SimError) as caught:
        collect(flow,wid)
    assert caught.value.code == 'budget_exhausted'
    assert len(llm.calls)==1 and len(search.calls)==1 and not store.captures(wid)


def test_uncertain_provider_never_refunded_or_replayed(state):
    store,wid = state
    flow,llm,_ = workflow(store,[ProviderError('transport_outcome_uncertain')])
    with pytest.raises(ProviderError): collect(flow,wid)
    with pytest.raises(SimError) as caught: collect(flow,wid)
    assert caught.value.code == 'operation_consumed' and len(llm.calls)==1
    with store.db() as db:
        row=db.execute('SELECT status,result FROM operations').fetchone()
    assert row['status']=='uncertain' and json.loads(row['result'])['error_type']=='ProviderError'


def test_selection_cannot_invent_source(state):
    store,wid=state
    flow,_,_=workflow(store,[{'queries':['water']},{'selected_urls':['https://invented.example/x']}])
    with pytest.raises(SimError) as caught: collect(flow,wid)
    assert caught.value.code=='invalid_selection' and store.usage(wid)['capture_calls']['reserved']==0


def test_capture_failure_keeps_reserved_attempt_and_partial_result(state):
    store,wid=state
    def fail(url): raise OSError('fixture-network-failure')
    flow,_,_=workflow(store,[{'queries':['water']},{'selected_urls':[URL]}],fetch=fail)
    result=collect(flow,wid)
    assert result['status']=='partial' and result['captured']==0 and result['failed']==1
    assert store.usage(wid)['capture_calls']['reserved']==1


def test_research_uses_frozen_tools_without_search_or_fetch(state):
    store,wid,doc,snapshot=frozen(state)
    llm=ScriptedLLM([{'action':'search','query':'water'}, {'action':'open','document_id':doc},
        {'action':'finish','answer':'42 cm','citations':[{'document_id':doc,'quote':'Water level is 42 cm.'}]}])
    def prohibited(*a,**kw): raise AssertionError('Live collection called during replay')
    flow=Workflow(store,cost_admission=None,llm_factory=lambda *a:llm,search_factory=prohibited,fetch=prohibited)
    run=research(flow,snapshot)
    assert run['status']=='completed' and run['cost_usd'] is None
    assert run['observed_models']==['fixture-served-model']*3 and llm.closed
    assert run['trace'][0]['observation']['results'][0]['id']==doc
    assert store.run(run['id'])==run
    assert store.usage(wid)['search_calls']['reserved']==0


@pytest.mark.parametrize('quote,opened', [('made up',True),('Water level is 42 cm.',False)])
def test_invalid_citations_recorded(state,quote,opened):
    store,wid,doc,snapshot=frozen(state)
    actions=([{'action':'open','document_id':doc}] if opened else []) + [
        {'action':'finish','answer':'claim','citations':[{'document_id':doc,'quote':quote}]}]
    flow,_,_=workflow(store,actions)
    run=research(flow,snapshot)
    assert run['status']=='invalid_citations' and len(run['citation_errors'])==1


def test_unknown_action_and_missing_document_are_visible(state):
    store,wid,doc,snapshot=frozen(state)
    flow,_,_=workflow(store,[{'action':'browse','url':'https://outside.example'}, {'action':'open','document_id':'0'*32}])
    run=research(flow,snapshot,max_steps=2)
    assert run['status']=='step_limit'
    assert [s['observation']['error']['code'] for s in run['trace']]==['unknown_action','archive_miss']


def test_comparison_same_snapshot_and_unknown_cost(state):
    store,wid,doc,snapshot=frozen(state)
    flow,_,_=workflow(store,[{'action':'finish','answer':'Insufficient evidence','citations':[]}])
    a=research(flow,snapshot,'a')
    flow,_,_=workflow(store,[{'action':'finish','answer':'Insufficient evidence','citations':[]}])
    b=research(flow,snapshot,'b')
    result=compare(store,[a['id'],b['id']])
    assert result['snapshot_id']==snapshot and all(m['cost_usd'] is None for m in result['metrics'])
    store.add_capture(wid,**page('https://example.org/other').__dict__)
    second=store.freeze(wid)['id']
    flow,_,_=workflow(store,[{'action':'finish','answer':'Insufficient evidence','citations':[]}])
    c=research(flow,second,'c')
    with pytest.raises(SimError) as caught: compare(store,[a['id'],c['id']])
    assert caught.value.code=='snapshot_mismatch'
