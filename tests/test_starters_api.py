from fastapi.testclient import TestClient
from web_research_sim.api import create_app


def test_starter_installs_without_calls_or_hidden_answers(tmp_path,monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN',raising=False)
    with TestClient(create_app(tmp_path),base_url='http://localhost') as c:
        listing=c.get('/api/starters').json()['starters']
        assert {s['slug'] for s in listing}=={'river','energy','civic'}
        assert all(s['synthetic'] for s in listing)
        result=c.post('/api/starters/energy')
        assert result.status_code==201
        data=result.json();assert data['synthetic']
        world=c.get('/api/worlds/'+data['world_id']).json()
        assert world['runs']==[]
        assert all(v['reserved']==0 for v in world['usage'].values())
        assert len(world['snapshots'])==3
        assert 'reference_items' not in result.text and 'label_review_status' not in result.text
        assert c.post('/api/starters/not-a-starter').status_code==400


def test_starter_mutation_requires_workspace_auth(tmp_path,monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN','fixture-private')
    with TestClient(create_app(tmp_path),base_url='http://localhost') as c:
        assert c.post('/api/starters/river').status_code==401
        assert c.get('/api/starters').status_code==401
