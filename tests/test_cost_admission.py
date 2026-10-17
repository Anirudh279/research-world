import json
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.cost_admission import ProviderAdmission
from web_research_sim.workflow import Workflow
from web_research_sim.providers import ModelReply


def configured(tmp_path):
    store=Store(tmp_path);wid=store.create_world('Question')['id']
    gate=ProviderAdmission(tmp_path);gate.ledger.allocate(100)
    policy={'schema':'provider-cost-policy-v1','expires_at':'2099-01-01T00:00:00Z','bounds':[{'kind':'model_calls','provider':'fixture','model':'fixture','max_request_bytes':1000,'max_output_tokens':2048,'max_cost_micro_usd':60,'operator_reviewed':True,'basis':'Synthetic test bound; not provider pricing'}]}
    (tmp_path/'cost-policy.json').write_text(json.dumps(policy))
    return store,wid,gate


def test_default_workflow_refuses_unbounded_transmission(tmp_path):
    store=Store(tmp_path);wid=store.create_world('Q')['id'];calls=[]
    with pytest.raises(SimError) as exc:
        Workflow(store).call(wid,'model_calls','one',{'provider':'fixture','model':'fixture'},lambda:calls.append(1))
    assert exc.value.code=='cost_policy_required' and calls==[]


def test_bound_reserved_before_call_and_unknown_cost_retained(tmp_path):
    store,wid,gate=configured(tmp_path);calls=[]
    def provider():
        assert gate.ledger.summary()['charged']==60
        calls.append(1)
        return ModelReply({'ok':True},{},'fixture',{'input_tokens':2,'output_tokens':3})
    flow=Workflow(store)
    request={'provider':'fixture','model':'fixture','messages':[]}
    flow.call(wid,'model_calls','one',request,provider)
    assert gate.ledger.summary()['charged']==60
    assert gate.ledger.operation(wid+':one')['usage']['input_tokens']==2
    with pytest.raises(SimError) as exc: flow.call(wid,'model_calls','two',request,provider)
    assert exc.value.code=='cost_budget_exhausted' and calls==[1]


def test_timeout_retains_bound_across_restart(tmp_path):
    store,wid,gate=configured(tmp_path)
    def uncertain(): raise TimeoutError('private provider detail')
    with pytest.raises(TimeoutError):
        Workflow(store).call(wid,'model_calls','one',{'provider':'fixture','model':'fixture'},uncertain)
    restarted=ProviderAdmission(tmp_path)
    assert restarted.ledger.summary()['charged']==60
    assert restarted.ledger.operation(wid+':one')['status']=='uncertain'


@pytest.mark.parametrize('change,code',[
    ({'max_request_bytes':1},'cost_request_limit'),
    ({'operator_reviewed':False},'cost_bound_unreviewed'),
    ({'max_output_tokens':4096},'cost_output_limit'),
    ({'max_cost_micro_usd':0},'cost_bound_required'),
    ({'model':'different'},'cost_bound_required')])
def test_invalid_envelope_never_transmits(tmp_path,change,code):
    store,wid,gate=configured(tmp_path);p=tmp_path/'cost-policy.json'
    policy=json.loads(p.read_text());policy['bounds'][0].update(change);p.write_text(json.dumps(policy))
    calls=[]
    with pytest.raises(SimError) as exc:
        Workflow(store).call(wid,'model_calls','one',{'provider':'fixture','model':'fixture'},lambda:calls.append(1))
    assert exc.value.code==code and calls==[]
    assert gate.ledger.summary()['charged']==0
