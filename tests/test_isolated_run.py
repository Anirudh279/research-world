from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.isolated_run import run_once


def args():
    return dict(request_key='stable',provider='openai',model='fixture',image_id='sha256:'+'a'*64)


def test_uncertain_submission_survives_restart_and_never_resubmits(tmp_path):
    store=Store(tmp_path);calls=[]
    def uncertain(*a,**k):
        calls.append(1)
        raise ConnectionError('private detail must not escape')
    with pytest.raises(SimError) as error:run_once(store,'b'*64,runner=uncertain,**args())
    assert 'private detail' not in str(error.value)
    result=run_once(Store(tmp_path),'b'*64,runner=uncertain,**args())
    assert result['status']=='uncertain' and len(calls)==1
    with pytest.raises(SimError) as error:run_once(store,'c'*64,runner=uncertain,**args())
    assert error.value.code=='idempotency_conflict'


def test_concurrent_same_key_admits_only_one_run(tmp_path):
    store=Store(tmp_path);entered=threading.Event();release=threading.Event();calls=[]
    def runner(*a,**k):
        calls.append(1);entered.set();assert release.wait(3)
        return {'id':'d'*32}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(run_once,store,'b'*64,runner=runner,**args())
        assert entered.wait(3)
        observed=run_once(store,'b'*64,runner=runner,**args())
        assert observed['status']=='uncertain'
        release.set();assert first.result()['status']=='completed'
    assert len(calls)==1
    assert run_once(Store(tmp_path),'b'*64,runner=runner,**args())['run_id']=='d'*32
