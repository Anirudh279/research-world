from fastapi.testclient import TestClient
from web_research_sim.api import create_app
from web_research_sim.costs import CostLedger


def test_cost_status_requires_auth_and_never_returns_policy_contents(tmp_path,monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN','fixture-workspace-token')
    (tmp_path/'cost-policy.json').write_text('PRIVATE-POLICY-FIXTURE')
    ledger=CostLedger(tmp_path);ledger.allocate(1000000)
    ledger.reserve('fixture',provider='fixture',model='fixture',request={},max_cost_micro_usd=100000,bound_basis='Private contract fixture')
    with TestClient(create_app(tmp_path),base_url='http://localhost') as client:
        assert client.get('/api/costs').status_code==401
        result=client.get('/api/costs',headers={'Authorization':'Bearer fixture-workspace-token'})
        assert result.status_code==200
        data=result.json()
        assert data['allocation']==1000000 and data['charged']==100000 and data['remaining']==900000
        assert data['actual_cost_unknown_operations']==1 and data['policy_file_present']
        assert 'PRIVATE-POLICY' not in result.text and 'Private contract' not in result.text
        # Presence is deliberately not a policy-validation claim.
        assert 'Presence only' in data['policy_status_meaning']
