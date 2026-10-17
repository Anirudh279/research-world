import pytest
from web_research_sim.core import Store, SimError


def test_budget_changes_preserve_consumed_calls(tmp_path):
    store = Store(tmp_path)
    wid = store.create_world("Budget fixture")["id"]
    store.reserve(wid, "model_calls", "first", {})
    with pytest.raises(SimError) as err:
        store.set_budgets(wid, {"model_calls": 0, "search_calls": 0, "capture_calls": 0})
    assert err.value.code == "budget_below_usage"
    store.set_budgets(wid, {"model_calls": 1, "search_calls": 0, "capture_calls": 0})
    with pytest.raises(SimError) as err:
        store.reserve(wid, "model_calls", "second", {})
    assert err.value.code == "budget_exhausted"
    store.set_budgets(wid, {"model_calls": 2, "search_calls": 0, "capture_calls": 0})
    store.reserve(wid, "model_calls", "second", {})
    assert store.usage(wid)["model_calls"] == {"reserved": 2, "limit": 2}


def test_native_only_world_zero_hosted_budgets(tmp_path):
    from web_research_sim.core import Store, SimError
    import pytest
    store=Store(tmp_path)
    world=store.create_world('Native subscription collection', {'model_calls':0,'search_calls':0,'capture_calls':2})
    for kind in ('model_calls','search_calls'):
        with pytest.raises(SimError) as exc:
            store.reserve(world['id'],kind,kind,{'provider':'forbidden'})
        assert exc.value.code=='budget_exhausted'
    assert store.reserve(world['id'],'capture_calls','capture',{'url':'https://example.org'})
