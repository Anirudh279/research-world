"""Transport contract tests only; these do not prove live LLM integration."""
import httpx
import pytest
from web_research_sim.client import Client, APIError


def test_bearer_and_json_request():
    def handle(request):
        assert request.headers['authorization'] == 'Bearer test-token'
        assert request.url.path == '/api/worlds'
        assert b'question' in request.content
        return httpx.Response(200, json={'id': 'world-1'})
    with Client('http://test', token='test-token', transport=httpx.MockTransport(handle)) as client:
        assert client.post('/api/worlds', {'question': 'Example?'}) == {'id': 'world-1'}


def test_structured_error():
    with Client('http://test', transport=httpx.MockTransport(lambda _: httpx.Response(409, json={'error': {'code': 'budget', 'message': 'Budget exhausted'}}))) as client:
        with pytest.raises(APIError, match='Budget exhausted'):
            client.post('/api/worlds/w/collect')


def test_non_json_error_is_actionable():
    with Client('http://test', transport=httpx.MockTransport(lambda _: httpx.Response(502, text='<html>upstream error</html>'))) as client:
        with pytest.raises(APIError, match='non-JSON'):
            client.get('/api/status')


def test_env_configuration(monkeypatch):
    monkeypatch.setenv('WR_SIM_URL', 'http://example.test')
    monkeypatch.setenv('WR_SIM_TOKEN', 'environment-token')
    def handle(request):
        assert request.url.host == 'example.test'
        assert request.headers['authorization'] == 'Bearer environment-token'
        return httpx.Response(200, json={'version': 'test'})
    with Client(transport=httpx.MockTransport(handle)) as client:
        assert client.get('/api/status')['version'] == 'test'


def test_cli_inspects_existing_job_without_post(monkeypatch, capsys):
    from web_research_sim import cli
    def handle(request):
        assert request.method == 'GET'
        assert request.url.path == '/api/jobs/existing-job'
        return httpx.Response(200, json={'id': 'existing-job', 'status': 'interrupted'})
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: Client('http://test', transport=httpx.MockTransport(handle)))
    assert cli.main(['job', 'existing-job']) == 1
    assert 'interrupted' in capsys.readouterr().out


def test_dashboard_assets_and_empty_world_contract(tmp_path, monkeypatch):
    """Real local API/store; no external model, search or capture operations."""
    from fastapi.testclient import TestClient
    from web_research_sim.api import create_app
    monkeypatch.delenv('WR_SIM_TOKEN', raising=False)
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)
    with TestClient(create_app(tmp_path)) as browser:
        assert browser.get('/').status_code == 200
        assert 'Evidence, held still.' in browser.get('/').text
        assert browser.get('/static/app.js').status_code == 200
        assert browser.get('/static/style.css').status_code == 200
        world = browser.post('/api/worlds', json={'question': 'Offline contract test?', 'budgets': {'model_calls': 18, 'search_calls': 5, 'capture_calls': 20}}).json()
        detail = browser.get('/api/worlds/' + world['id']).json()
        assert detail['captures'] == []
        assert detail['runs'] == []
        assert detail['snapshots'] == []
        assert detail['usage']['model_calls'] == {'limit': 18, 'reserved': 0}
