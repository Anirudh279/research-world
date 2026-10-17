"""Deterministic injected providers: no hosted inference or live capture claim."""
import time
import json
import pytest
from fastapi.testclient import TestClient
from web_research_sim.api import create_app
from web_research_sim.core import Store,SimError,encoded
from web_research_sim.providers import ModelReply,SearchReply
from web_research_sim.workflow import Workflow

CONFIG=dict(model_provider='fixture',model='explicit',search_provider='fixture',max_queries=2,max_pages=2)

class Providers:
    def __init__(self):self.model_calls=0;self.search_calls=[];self.factories=0;self.plan={'directions':['Direct evidence','Counterevidence'],'queries':[' water 2026 ','water 2026','alternatives']}
    def llm(self,*args):return self
    def complete(self,messages):
        self.model_calls+=1
        return ModelReply(self.plan,{},'observed',{})
    def search_factory(self,*args):self.factories+=1;return self
    def search(self,query,limit=5):self.search_calls.append(query);return SearchReply([],{},'fixture')
    def close(self):pass


def poll(client,jid):
    end=time.monotonic()+3
    while time.monotonic()<end:
        job=client.get('/api/jobs/'+jid).json()
        if job['status'] in ('completed','failed','interrupted'):return job
        time.sleep(.01)
    pytest.fail('job timeout')

@pytest.fixture
def api(tmp_path,monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN',raising=False);monkeypatch.delenv('WR_SIM_PUBLIC_HOST',raising=False)
    providers=Providers()
    def factory(store):return Workflow(store,llm_factory=providers.llm,search_factory=providers.search_factory,cost_admission=None,fetch=lambda u:pytest.fail('Unexpected capture'))
    with TestClient(create_app(tmp_path,workflow_factory=factory)) as client:
        wid=client.post('/api/worlds',json={'question':'Water?'}).json()['id']
        yield client,wid,providers,Store(tmp_path)


def plan(client,wid):
    response=client.post(f'/api/worlds/{wid}/plan',json=CONFIG,headers={'Idempotency-Key':'plan-one'})
    assert response.status_code==202
    return poll(client,response.json()['id'])


def test_plan_only_then_exact_approval_once(api):
    client,wid,p,store=api
    job=plan(client,wid);assert job['status']=='completed'
    result=job['result'];assert result['queries']==['water 2026','alternatives']
    assert p.model_calls==1 and p.factories==0 and p.search_calls==[]
    replay=client.post(f'/api/worlds/{wid}/plan',json=CONFIG,headers={'Idempotency-Key':'plan-one'}).json()
    assert replay['id']==job['id'] and replay['replayed'] and p.model_calls==1
    body={**CONFIG,'plan_job_id':job['id']}
    approved=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'approval-one'})
    assert approved.status_code==202
    completed=poll(client,approved.json()['id']);assert completed['status']=='completed'
    assert p.model_calls==1 and p.search_calls==result['queries']
    same=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'approval-one'})
    assert same.json()['id']==approved.json()['id'] and same.json()['replayed']
    again=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'approval-two'})
    assert again.status_code==409 and again.json()['error']['code']=='plan_consumed'
    assert len(p.search_calls)==2


@pytest.mark.parametrize('mutation',['config','world','queries','hash','pending','failed','kind'])
def test_mismatch_or_tampering_rejected_before_search(api,mutation):
    client,wid,p,store=api;job=plan(client,wid);body={**CONFIG,'plan_job_id':job['id']}
    if mutation=='config':body['max_pages']=3
    elif mutation=='world':wid=client.post('/api/worlds',json={'question':'Other?'}).json()['id']
    else:
        with store.db(write=True) as db:
            if mutation in ('queries','hash'):
                result=job['result'];result['queries' if mutation=='queries' else 'plan_sha256']=['changed'] if mutation=='queries' else '0'*64
                db.execute('UPDATE jobs SET result=? WHERE id=?',(encoded(result).decode(),job['id']))
            elif mutation in ('pending','failed'):db.execute('UPDATE jobs SET status=? WHERE id=?',('running' if mutation=='pending' else 'failed',job['id']))
            else:db.execute("UPDATE jobs SET kind='collection' WHERE id=?",(job['id'],))
    response=client.post(f'/api/worlds/{wid}/collect',json=body)
    assert response.status_code==409 and p.factories==0 and p.model_calls==1


@pytest.mark.parametrize('bad',[{'queries':[]},{'queries':['x'],'directions':'bad'},{'queries':['x'*601]},{'queries':['x'],'directions':['z'*1001]}])
def test_invalid_model_plan_spends_only_model_call(api,bad):
    client,wid,p,store=api;p.plan=bad
    job=plan(client,wid);assert job['status']=='failed'
    assert p.model_calls==1 and p.factories==0 and p.search_calls==[]
    repeated=client.post(f'/api/worlds/{wid}/plan',json=CONFIG,headers={'Idempotency-Key':'plan-one'}).json()
    assert repeated['id']==job['id'] and p.model_calls==1


def test_uncertain_collection_claim_cannot_be_reused(api):
    client,wid,p,store=api;job=plan(client,wid)
    def fail(*a,**k):raise RuntimeError('fixture provider uncertainty')
    p.search=fail
    body={**CONFIG,'plan_job_id':job['id']}
    submitted=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'uncertain'}).json()
    assert poll(client,submitted['id'])['status']=='failed'
    assert client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'uncertain'}).json()['id']==submitted['id']
    assert client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'new-key'}).status_code==409


def test_concurrent_approvals_only_one_claim(api):
    from concurrent.futures import ThreadPoolExecutor
    client,wid,p,store=api;job=plan(client,wid);body={**CONFIG,'plan_job_id':job['id']}
    def approve(key):return client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':key})
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(approve,['a','b']))
    assert sorted(r.status_code for r in results)==[202,409]
    accepted=next(r for r in results if r.status_code==202)
    assert poll(client,accepted.json()['id'])['status']=='completed'
    listing=client.get(f'/api/worlds/{wid}/plans').json()['plans']
    assert listing==[{'id':job['id'],'status':'completed','collection_job_id':accepted.json()['id']}]
    assert len(p.search_calls)==2


def test_dispatch_uncertainty_consumes_plan_without_search(api,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    client,wid,p,store=api;job=plan(client,wid);body={**CONFIG,'plan_job_id':job['id']}
    def fail(*a,**k):raise RuntimeError('dispatch fixture')
    with monkeypatch.context() as patch:
        patch.setattr(ThreadPoolExecutor,'submit',fail)
        first=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'dispatch'})
    assert first.json()['status']=='interrupted' and p.factories==0
    repeated=client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'dispatch'})
    assert repeated.json()['id']==first.json()['id'] and repeated.json()['replayed']
    assert client.post(f'/api/worlds/{wid}/collect',json=body,headers={'Idempotency-Key':'new'}).status_code==409
    assert p.factories==0
