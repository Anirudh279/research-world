"""Actual API and durable session contract, using only controlled local evidence."""
import json
from fastapi.testclient import TestClient
import pytest
from web_research_sim.api import create_app
from web_research_sim.client import Client


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN', raising=False)
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)
    app = create_app(tmp_path)
    store = app.state.store
    w = store.create_world('What color is the fixture?')
    doc = store.add_capture(w['id'], url='https://example.org/fixture', body=b'The fixture is blue.', text='The fixture is blue.', captured_at='2026-01-01T00:00:00+00:00')
    snap = store.freeze(w['id'])
    return app, snap['id'], doc['id']


def start(client, snapshot, key='start-one', limit=3):
    response = client.post(f'/api/snapshots/{snapshot}/sessions', json={'provider': 'native-test', 'model': 'declared-fixture', 'tool_limit': limit, 'request_id': key})
    assert response.status_code == 201, response.text
    return response.json()


def test_session_api_trace_submission_reset_and_compare(world):
    app, snapshot, doc = world
    with TestClient(app) as client:
        session = start(client, snapshot)
        sid = session['id']
        assert session['snapshot_id'] == snapshot
        assert session['model_identity'] == 'declared_unverified'
        assert 'not_network_isolated' in session['isolation']
        assert start(client, snapshot)['id'] == sid
        call = {'operation_key': 'open-first', 'action': 'open', 'arguments': {'document_id': doc}}
        response = client.post(f'/api/sessions/{sid}/tools', json=call)
        assert response.json()['ok'] is True
        assert client.post(f'/api/sessions/{sid}/tools', json=call).json() == response.json()
        assert client.get(f'/api/sessions/{sid}').json()['tool_calls'] == 1
        changed = {**call, 'action': 'search'}
        assert client.post(f'/api/sessions/{sid}/tools', json=changed).status_code == 409
        answer = {'answer': 'Blue.', 'citations': [{'document_id': doc, 'quote': 'The fixture is blue.'}]}
        run = client.post(f'/api/sessions/{sid}/submit', json=answer).json()
        assert run['status'] == 'completed'
        assert client.post(f'/api/sessions/{sid}/submit', json=answer).json()['id'] == run['id']
        assert client.post(f'/api/sessions/{sid}/submit', json={**answer, 'answer': 'Changed'}).status_code == 409
        reset = client.post(f'/api/sessions/{sid}/reset', json={'request_id': 'new-attempt'}).json()
        assert reset['id'] != sid
        assert reset['parent_id'] == sid
        assert reset['tool_calls'] == 0
        assert client.post(f'/api/sessions/{sid}/reset', json={'request_id': 'new-attempt'}).json()['id'] == reset['id']
        # A new session must earn its own evidence visibility; parent quotes do
        # not silently become visible to the new agent.
        second = client.post(f"/api/sessions/{reset['id']}/submit", json=answer).json()
        assert second['status'] == 'invalid_citations'
        comparison = client.post('/api/compare', json={'run_ids': [run['id'], second['id']]})
        assert comparison.status_code == 200
        assert comparison.json()['snapshot_id'] == snapshot


def test_session_budget_and_control_field_rejection(world):
    app, snapshot, doc = world
    with TestClient(app) as client:
        sid = start(client, snapshot, limit=1)['id']
        path = f'/api/sessions/{sid}/tools'
        assert client.post(path, json={'operation_key': 'bad', 'action': 'open', 'arguments': {'session_id': 'override'}}).status_code == 400
        assert client.get(f'/api/sessions/{sid}').json()['tool_calls'] == 0
        assert client.post(path, json={'operation_key': 'one', 'action': 'open', 'arguments': {'document_id': doc}}).json()['ok']
        assert client.post(path, json={'operation_key': 'two', 'action': 'search', 'arguments': {'query': 'blue'}}).status_code == 409
        assert client.post(f'/api/snapshots/{snapshot}/sessions', json={'provider': 'test', 'model': 'test'}).status_code == 422


def test_cli_native_session_commands_use_exact_contract(monkeypatch, capsys, tmp_path):
    import httpx
    from web_research_sim import cli
    seen = []
    def handle(request):
        seen.append((request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(200, json={'id': 'session-test', 'status': 'completed'})
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: Client('http://test', transport=httpx.MockTransport(handle)))
    assert cli.main(['session', 'start', 'snapshot', '--provider', 'native', '--model', 'test', '--request-key', 'stable-start']) == 0
    assert seen[-1] == ('/api/snapshots/snapshot/sessions', {'provider': 'native', 'model': 'test', 'tool_limit': 30, 'request_id': 'stable-start'})
    assert cli.main(['session', 'tool', 'session-test', 'search', '--arguments', '{"query":"blue"}', '--request-key', 'stable-tool']) == 0
    assert seen[-1][1] == {'operation_key': 'stable-tool', 'action': 'search', 'arguments': {'query': 'blue'}}
    assert cli.main(['session', 'reset', 'session-test', '--request-key', 'stable-reset']) == 0
    assert seen[-1][1] == {'request_id': 'stable-reset'}
    path = tmp_path / 'answer.json'
    path.write_text('{"answer":"Insufficient evidence.","citations":[]}')
    assert cli.main(['session', 'submit', 'session-test', '--file', str(path)]) == 0
    assert seen[-1][0] == '/api/sessions/session-test/submit'
    assert cli.main(['session', 'tool', 'session-test', 'search', '--arguments', '[]']) == 1
