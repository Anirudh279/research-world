"""Controlled clocks/fixtures; these tests do not replace the real 24-hour soak."""
from datetime import datetime,timezone,timedelta
import concurrent.futures
import threading
import pytest
from web_research_sim.core import Store,SimError
from web_research_sim.scheduler import ScheduleManager


class Clock:
    def __init__(self): self.value=datetime(2026,1,1,tzinfo=timezone.utc)
    def __call__(self): return self.value.isoformat()
    def advance(self,seconds): self.value+=timedelta(seconds=seconds)


class Work:
    def __init__(self): self.calls=[]
    def capture_discovered(self,wid,job,**config):
        self.calls.append((wid,job,config)); return {'status':'completed','captured':1}
    def collect(self,wid,job,**config):
        self.calls.append((wid,job,config)); return {'status':'completed','captured':2}


@pytest.fixture
def fixture(tmp_path):
    clock=Clock(); store=Store(tmp_path,clock=clock); work=Work()
    wid=store.create_world('Question')['id']
    manager=ScheduleManager(store,work,clock=clock,owner_id='fixture-owner')
    return store,manager,work,clock,wid


def create(f,**overrides):
    store,manager,work,clock,wid=f
    args=dict(world_id=wid,kind='native_discovered',config={'sources':[{'url':'https://example.org/a'}],'native_client':'fixture'},interval_seconds=10,max_runs=5,start_at=clock(),request_id='create')
    args.update(overrides)
    return manager.create(**args)


def test_due_slots_idempotent_create_and_finish(fixture):
    store,m,w,c,wid=fixture
    schedule=create(fixture,max_runs=2)
    assert create(fixture,max_runs=2)['id']==schedule['id']
    a=m.tick()[0]; assert a['status']=='completed' and a['slot_index']==0
    assert m.tick()==[]
    c.advance(10); b=m.tick()[0]
    assert b['slot_index']==1 and b['job_id']!=a['job_id']
    assert m.status(schedule['id'])['status']=='completed' and len(w.calls)==2


def test_pause_resume_skips_missed_not_burst(fixture):
    store,m,w,c,wid=fixture
    s=create(fixture)
    m.pause(s['id']); c.advance(35)
    assert m.tick()==[]
    m.resume(s['id']); receipt=m.tick()[0]
    assert receipt['slot_index']==3 and len(w.calls)==1 and m.tick()==[]
    assert m.status(s['id'])['skipped_count']==3


def test_completely_missed_schedule_never_catches_up(fixture):
    store,m,w,c,wid=fixture
    s=create(fixture); c.advance(100)
    assert m.tick()==[] and not w.calls
    assert m.status(s['id'])['skipped_count']==5 and m.status(s['id'])['status']=='completed'


def test_claim_before_work_and_uncertainty_never_redispatched(fixture):
    store,m,w,c,wid=fixture
    s=create(fixture)
    class Crash(BaseException): pass
    def crash(world,job,**config):
        with store.db() as db:
            row=db.execute('SELECT job_id,status FROM schedule_slots').fetchone()
        assert row['job_id']==job and row['status']=='claimed'
        raise Crash()
    w.capture_discovered=crash
    with pytest.raises(Crash): m.tick()
    c.advance(100)
    restarted=ScheduleManager(Store(store.root,clock=c),Work(),clock=c,owner_id='fixture-owner')
    assert restarted.tick()==[]  # Age alone is never death evidence.
    with pytest.raises(SimError): restarted.recover_owned()
    receipts=restarted.recover_owned(exclusive_owner_confirmed=True)
    assert len(receipts)==1
    assert restarted.status(s['id'])['slots'][0]['status']=='interrupted'
    assert restarted.tick()==[] and restarted.status(s['id'])['skipped_count']==4


def test_other_runner_namespace_not_recovered(fixture):
    store,m,w,c,wid=fixture
    s=create(fixture)
    other=ScheduleManager(store,Work(),clock=c,owner_id='other-owner')
    assert other.list()==[] and other.tick()==[] and other.recover_owned(exclusive_owner_confirmed=True)==[]
    with pytest.raises(SimError) as exc: other.status(s['id'])
    assert exc.value.code=='schedule_owner'


def test_no_overlap_pause_preserves_inflight(fixture):
    store,m,w,c,wid=fixture
    s=create(fixture); entered=threading.Event(); release=threading.Event()
    def blocking(*a,**kw): entered.set(); assert release.wait(5); return {'ok':True}
    w.capture_discovered=blocking
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(m.tick); assert entered.wait(5)
        c.advance(10); assert m.tick()==[]
        m.pause(s['id']); release.set()
        assert future.result()[0]['status']=='completed'
    assert m.status(s['id'])['status']=='paused' and m.tick()==[]


def test_workflow_budgets_still_stop_calls(fixture):
    store,m,w,c,wid=fixture
    store2=Store(store.root,clock=c)
    wid2=store2.create_world('Limited',{'model_calls':1,'search_calls':1,'capture_calls':1})['id']
    calls=[]
    def admitted(world,job,**kw):
        op=store.reserve(world,'capture_calls',job,{'fixture':True})
        calls.append(job); store.finish(op,{'ok':True}); return {'ok':True}
    w.capture_discovered=admitted
    s=create(fixture,world_id=wid2,max_runs=2)
    assert m.tick()[0]['status']=='completed'
    c.advance(10); receipt=m.tick()[0]
    assert receipt['status']=='uncertain' and receipt['result']['error_code']=='budget_exhausted'
    assert len(calls)==1 and store.usage(wid2)['capture_calls']['reserved']==1


def test_collection_dispatch_and_request_conflict(fixture):
    store,m,w,c,wid=fixture
    cfg={'model_provider':'openai','model':'explicit-fixture','search_provider':'brave'}
    s=create(fixture,kind='collection',config=cfg)
    assert m.tick()[0]['result']['captured']==2 and w.calls[0][2]==cfg
    with pytest.raises(SimError) as exc: create(fixture,kind='collection',config={**cfg,'model':'different'})
    assert exc.value.code=='operation_conflict'
