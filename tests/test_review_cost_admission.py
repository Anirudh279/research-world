"""Admission regressions; trusted-callback boundary demonstrated without external calls."""
import json

import httpx

from web_research_sim.core import Store
from web_research_sim.cost_admission import ProviderAdmission
from web_research_sim.providers import OpenAI, ModelReply
from web_research_sim.workflow import Workflow


def setup(tmp_path, expires='2099-01-01T00:00:00Z'):
    store = Store(tmp_path)
    world = store.create_world('Review fixture')['id']
    admission = ProviderAdmission(tmp_path)
    admission.ledger.allocate(1000)
    (tmp_path / 'cost-policy.json').write_text(json.dumps({
        'schema': 'provider-cost-policy-v1', 'expires_at': expires, 'bounds': [{
            'kind': 'model_calls', 'provider': 'openai', 'model': 'reviewed-model',
            'max_request_bytes': 1000, 'max_output_tokens': 2048,
            'max_cost_micro_usd': 100, 'operator_reviewed': True,
            'basis': 'Synthetic review bound; no real pricing or transmission'}]}))
    return store, world, admission


def test_review_trusted_python_callback_is_outside_default_http_envelope(tmp_path):
    # Intentionally mismatched Python callback: NOT remotely selectable through
    # current default HTTP routes. Documents the trusted-code boundary.
    store, world, admission = setup(tmp_path)
    outgoing = []
    def handler(request):
        outgoing.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'completed', 'model': 'different-model',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = OpenAI('fixture-key', 'different-model', client=client)
        request = {'provider': 'openai', 'model': 'reviewed-model', 'messages': [{'role': 'user', 'content': 'short'}]}
        Workflow(store, cost_admission=admission).call(world, 'model_calls', 'mismatch', request,
            lambda: adapter.complete([{'role': 'user', 'content': 'long' * 1000}], max_tokens=4096))
    assert outgoing[0]['model'] == 'different-model'
    assert outgoing[0]['max_output_tokens'] == 4096
    assert len(json.dumps(outgoing[0]).encode()) > 1000
    assert admission.ledger.operation(world + ':mismatch')['max_cost'] == 100


def test_review_policy_can_expire_during_reservation_before_transmission(tmp_path, monkeypatch):
    from web_research_sim import cost_admission
    store, world, admission = setup(tmp_path, expires='2026-01-01T00:00:01Z')
    clock = ['2026-01-01T00:00:00Z']
    monkeypatch.setattr(cost_admission, 'now', lambda: clock[0])
    reserve = admission.ledger.reserve
    def delayed_reserve(*args, **kwargs):
        result = reserve(*args, **kwargs)
        clock[0] = '2026-01-01T00:00:02Z'
        return result
    monkeypatch.setattr(admission.ledger, 'reserve', delayed_reserve)
    sent_at = []
    def provider():
        sent_at.append(clock[0])
        return ModelReply({'ok': True}, {}, 'reviewed-model', {})
    import pytest
    from web_research_sim.core import SimError
    with pytest.raises(SimError) as exc:
        Workflow(store, cost_admission=admission).call(world, 'model_calls', 'expired',
            {'provider': 'openai', 'model': 'reviewed-model'}, provider)
    assert exc.value.code == 'cost_policy_expired'
    assert sent_at == []
    assert admission.ledger.summary()['charged'] == 100


def test_review_pretransmit_recheck_blocks_expiry_retains_charge(tmp_path, monkeypatch):
    import pytest
    from web_research_sim import cost_admission
    from web_research_sim.core import SimError
    store, world, admission = setup(tmp_path, expires='2026-01-01T00:00:01Z')
    clock = ['2026-01-01T00:00:00Z']
    monkeypatch.setattr(cost_admission, 'now', lambda: clock[0])
    request = {'provider': 'openai', 'model': 'reviewed-model'}
    key = admission.reserve(world, 'model_calls', 'recheck', request)
    clock[0] = '2026-01-01T00:00:02Z'
    with pytest.raises(SimError) as exc:
        admission.assert_ready(key, request)
    assert exc.value.code == 'cost_policy_expired'
    assert admission.ledger.summary()['charged'] == 100
    assert admission.ledger.operation(key)['status'] == 'reserved'


def test_review_pretransmit_recheck_blocks_changed_policy_or_request(tmp_path):
    import pytest
    from web_research_sim.core import SimError
    store, world, admission = setup(tmp_path)
    request = {'provider': 'openai', 'model': 'reviewed-model'}
    key = admission.reserve(world, 'model_calls', 'recheck', request)
    admission.assert_ready(key, request)
    with pytest.raises(SimError) as exc:
        admission.assert_ready(key, {**request, 'messages': []})
    assert exc.value.code == 'cost_operation_conflict'
    path = tmp_path / 'cost-policy.json'
    policy = json.loads(path.read_text())
    policy['bounds'][0]['operator_reviewed'] = False
    path.write_text(json.dumps(policy))
    with pytest.raises(SimError) as exc:
        admission.assert_ready(key, request)
    assert exc.value.code == 'cost_policy_changed'
    assert admission.ledger.summary()['charged'] == 100


def test_review_policy_replacement_after_reserve_stops_workflow(tmp_path, monkeypatch):
    import pytest
    from web_research_sim.core import SimError
    store, world, admission = setup(tmp_path)
    reserve = admission.reserve
    def replace_after_reserve(*args, **kwargs):
        key = reserve(*args, **kwargs)
        path = tmp_path / 'cost-policy.json'
        policy = json.loads(path.read_text())
        policy['bounds'][0]['operator_reviewed'] = False
        path.write_text(json.dumps(policy))
        return key
    monkeypatch.setattr(admission, 'reserve', replace_after_reserve)
    calls = []
    with pytest.raises(SimError) as exc:
        Workflow(store, cost_admission=admission).call(world, 'model_calls', 'replaced',
            {'provider': 'openai', 'model': 'reviewed-model'}, lambda: calls.append(True))
    assert exc.value.code == 'cost_policy_changed'
    assert calls == []
    assert admission.ledger.summary()['charged'] == 100


def test_review_terminal_reservation_cannot_pass_readiness_again(tmp_path):
    import pytest
    from web_research_sim.core import SimError
    store, world, admission = setup(tmp_path)
    request = {'provider': 'openai', 'model': 'reviewed-model'}
    key = admission.reserve(world, 'model_calls', 'terminal', request)
    admission.settle_unknown(key, 'uncertain')
    with pytest.raises(SimError) as exc:
        admission.assert_ready(key, request)
    assert exc.value.code == 'cost_operation_consumed'
