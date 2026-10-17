from fastapi.testclient import TestClient
from web_research_sim.api import create_app
from web_research_sim.sessions import SessionManager


def test_saved_run_evaluation_requires_auth_and_uses_recorded_exposure(tmp_path,monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN','fixture-workspace-token')
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST',raising=False)
    app=create_app(tmp_path);store=app.state.store
    world=store.create_world('What color?')['id']
    doc=store.add_capture(world,url='https://fixture.example',body=b'Blue.',text='Blue.',captured_at='2026-01-01T00:00:00Z')
    snapshot=store.freeze(world)['id'];sessions=SessionManager(store)
    session=sessions.create(snapshot,provider='fixture',model='fixture',request_id='one',tool_limit=3)
    sessions.tool(session['id'],'read','open',document_id=doc['id'])
    run=sessions.submit(session['id'],answer='Blue.',citations=[{'document_id':doc['id'],'quote':'Blue.'}])
    with TestClient(app) as client:
        url=f"/api/runs/{run['id']}/evaluation"
        assert client.get(url).status_code==401
        headers={'Authorization':'Bearer fixture-workspace-token'}
        report=client.get(url,headers=headers).json()
        assert report['metrics']['valid_citations']==1
        assert report['metrics']['reference_exact_recall'] is None
        assert report['reference_items']==[] and report['judge']['status']=='unknown'
        assert client.get(url,headers=headers).json()==report
        assert client.post(url,headers=headers,json={'trace':[]}).status_code==405
        (store.objects.root/doc['body_sha256']).write_bytes(b'corrupted')
        damaged=client.get(url,headers=headers).json()
        assert damaged['metrics']['valid_citations']==0
        assert damaged['citations'][0]['integrity_verified'] is False
