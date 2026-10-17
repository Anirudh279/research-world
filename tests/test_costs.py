from concurrent.futures import ThreadPoolExecutor

import pytest

from web_research_sim.core import SimError
from web_research_sim.costs import CostLedger


def reserve(ledger, key, cost=100, **kwargs):
    return ledger.reserve(key, provider='test', model='test-model', request=kwargs.get('request', {'query': key}),
                          max_cost_micro_usd=cost, bound_basis='Test-only explicit bound, no provider call')


def error(code, action):
    with pytest.raises(SimError) as exc:
        action()
    assert exc.value.code == code


def test_explicit_allocation_unknown_cost_and_no_price_inference(tmp_path):
    ledger = CostLedger(tmp_path)
    error('allocation_required', lambda: reserve(ledger, 'a'))
    ledger.allocate(1000)
    assert ledger.allocate(1000)['allocation'] == 1000
    error('allocation_conflict', lambda: ledger.allocate(1001))
    for value in (None, 0.5, True, -1, '1'):
        error('invalid_cost', lambda: reserve(ledger, 'bad', value))
    row = reserve(ledger, 'a')
    assert row['usage'] == {'input_tokens': None, 'output_tokens': None, 'search_credits': None, 'subscription_usage': None}
    assert row['actual_cost'] is None
    assert ledger.summary()['charged'] == 100
    assert 'query' not in row  # request identity retained as hash, not prompt content


def test_concurrent_instances_admit_only_budget_capacity(tmp_path):
    CostLedger(tmp_path).allocate(500)
    def attempt(i):
        try:
            return reserve(CostLedger(tmp_path), str(i))
        except SimError as exc:
            assert exc.code == 'cost_budget_exhausted'
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = [r for r in pool.map(attempt, range(30)) if r]
    assert len(admitted) == 5
    assert CostLedger(tmp_path).summary()['remaining'] == 0


def test_duplicate_race_and_fingerprint_conflict(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(1000)
    def attempt(_):
        try:
            return reserve(CostLedger(tmp_path), 'same')
        except SimError as exc:
            assert exc.code == 'cost_operation_consumed'
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len([r for r in pool.map(attempt, range(12)) if r]) == 1
    error('cost_operation_conflict', lambda: reserve(ledger, 'same', request={'changed': True}))
    error('cost_operation_conflict', lambda: reserve(ledger, 'same', 101))


def test_known_settlement_releases_only_confirmed_difference(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'one')
    row = ledger.settle('one', actual_cost_micro_usd=30, usage={'input_tokens': 50, 'search_credits': 1})
    assert row['charged'] == 30
    assert row['usage']['subscription_usage'] is None
    assert ledger.summary()['remaining'] == 70
    reserve(ledger, 'two', 70)
    error('cost_operation_consumed', lambda: ledger.settle('one', actual_cost_micro_usd=0))
    error('cost_operation_consumed', lambda: reserve(ledger, 'one'))


@pytest.mark.parametrize('outcome,actual', [('uncertain', None), ('uncertain', 0), ('failed', None), ('completed', None)])
def test_uncertainty_or_unknown_cost_never_refunded(tmp_path, outcome, actual):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'one')
    assert ledger.settle('one', outcome=outcome, actual_cost_micro_usd=actual)['charged'] == 100
    assert CostLedger(tmp_path).summary()['remaining'] == 0
    error('cost_budget_exhausted', lambda: reserve(ledger, 'two', 1))
    error('cost_operation_consumed', lambda: ledger.settle('one', actual_cost_micro_usd=0))


def test_provider_bound_breach_persisted_blocks_even_with_remaining_allocation(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(1000)
    reserve(ledger, 'one')
    row = ledger.settle('one', actual_cost_micro_usd=120)
    assert row['status'] == 'overspent'
    assert row['overspend_micro_usd'] == 20
    assert row['charged'] == 120
    assert CostLedger(tmp_path).summary()['admission_blocked']
    error('cost_bound_breached', lambda: reserve(ledger, 'two', 1))


def test_allocation_overrun_and_zero_cost_explicit(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'one')
    ledger.settle('one', actual_cost_micro_usd=150)
    assert ledger.summary()['allocation_overrun'] == 50
    other = CostLedger(tmp_path / 'separate')
    other.allocate(0)
    row = reserve(other, 'explicitly-free', 0)
    assert row['max_cost'] == 0
    assert row['actual_cost'] is None


def test_bad_usage_does_not_consume_settlement(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'one')
    error('invalid_usage', lambda: ledger.settle('one', actual_cost_micro_usd=1, usage={'input_tokens': True}))
    error('invalid_usage', lambda: ledger.settle('one', usage={'dollars': 1}))
    assert ledger.operation('one')['status'] == 'reserved'


def test_crash_after_reservation_retains_full_bound_on_reopen(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'possibly-sent')
    reopened = CostLedger(tmp_path)
    assert reopened.operation('possibly-sent')['status'] == 'reserved'
    assert reopened.summary()['charged'] == 100
    error('cost_operation_consumed', lambda: reserve(reopened, 'possibly-sent'))
    error('cost_budget_exhausted', lambda: reserve(reopened, 'different', 1))


def test_concurrent_terminal_reports_only_one_wins(tmp_path):
    ledger = CostLedger(tmp_path)
    ledger.allocate(100)
    reserve(ledger, 'one')
    def settle(i):
        try:
            return CostLedger(tmp_path).settle('one', actual_cost_micro_usd=i)
        except SimError as exc:
            assert exc.code == 'cost_operation_consumed'
    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [r for r in pool.map(settle, range(8)) if r]
    assert len(winners) == 1
    assert CostLedger(tmp_path).summary()['charged'] == winners[0]['actual_cost']
