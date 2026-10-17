import threading
import time

import pytest
from fastapi.testclient import TestClient

from web_research_sim.api import create_app
from web_research_sim.core import Store, encoded


class DummyWorkflow:
    def __init__(self, store):
        self.store = store
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def collect(self, world_id, job_id, **kwargs):
        self.calls.append((world_id, job_id, kwargs))
        self.started.set()
        assert self.release.wait(5), 'Test did not release dummy collection'
        if kwargs['model'] == 'fail':
            raise RuntimeError('SECRET: must not appear in public error')
        return {'captured': 2, 'job_id': job_id}

    def research(self, snapshot_id, job_id, **kwargs):
        return {'snapshot_id': snapshot_id, 'job_id': job_id, 'answer': 'Dummy answer'}


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN', raising=False)
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)


@pytest.fixture
def application(tmp_path):
    instances = []
    def factory(store):
        instance = DummyWorkflow(store)
        instances.append(instance)
        return instance
    app = create_app(tmp_path, workflow_factory=factory)
    yield app, instances[0]
    instances[0].release.set()


def poll(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get('/api/jobs/' + job_id).json()
        if result['status'] in {'completed', 'failed', 'interrupted'}:
            return result
        time.sleep(.01)
    pytest.fail('Job did not become terminal')


def test_host_origin_and_remote_auth_boundaries(application, monkeypatch):
    app, _ = application
    with TestClient(app) as client:
        assert client.get('/health', headers={'host': 'attacker.invalid'}).status_code == 403
        assert client.get('/health', headers={'origin': 'https://evil.invalid'}).status_code == 403
        assert client.get('/health', headers={'origin': 'http://testserver:9999'}).status_code == 403
        assert client.get('/health', headers={'origin': 'http://testserver'}).status_code == 200
        monkeypatch.setenv('WR_SIM_PUBLIC_HOST', 'research.example')
        assert client.get('/health', headers={'host': 'research.example'}).status_code == 503


def test_token_required_for_api_and_valid_token_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN', 'test-only-token')
    monkeypatch.setenv('WR_SIM_PUBLIC_HOST', 'research.example')
    with TestClient(create_app(tmp_path, workflow_factory=DummyWorkflow), base_url='https://research.example') as client:
        assert client.get('/api/worlds').status_code == 401
        assert client.get('/api/worlds', headers={'authorization': 'Bearer wrong'}).status_code == 401
        assert client.get('/api/worlds', headers={'authorization': 'Bearer test-only-token'}).json() == {'worlds': []}
        assert client.get('/health').status_code == 200


@pytest.mark.parametrize('body', [{'question': ''}, {'question': 'ok', 'unexpected': True}, {'question': 'ok', 'budgets': {'model_calls': -1}}])
def test_world_body_validation(application, body):
    app, _ = application
    with TestClient(app) as client:
        assert client.post('/api/worlds', json=body).status_code in {400, 422}
        assert client.get('/api/worlds').json() == {'worlds': []}


def test_body_size_and_malformed_content_length_fail_closed(application):
    app, _ = application
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.post('/api/worlds', content=b'x' * (128 * 1024 + 1)).status_code == 413
        malformed = client.post('/api/worlds', content=b'{}', headers={'content-length': 'not-a-number'})
        assert malformed.status_code == 400
        # TestClient sends this streamed body without Content-Length. A header-only
        # limit would allow the oversized payload to reach parsing instead.
        streamed = client.post('/api/worlds', content=iter([b'x' * 70000, b'y' * 70000]), headers={'content-type': 'application/json'})
        assert streamed.status_code == 413


@pytest.mark.parametrize('model,terminal', [('ok', 'completed'), ('fail', 'failed')])
def test_job_lifecycle_with_injected_workflow(application, model, terminal):
    app, dummy = application
    with TestClient(app) as client:
        world = client.post('/api/worlds', json={'question': 'Test question'}).json()['id']
        response = client.post(f'/api/worlds/{world}/collect', json={'model_provider': 'dummy', 'model': model, 'search_provider': 'dummy'})
        assert response.status_code == 202
        job_id = response.json()['id']
        try:
            assert dummy.started.wait(3)
            assert client.get('/api/jobs/' + job_id).json()['status'] == 'running'
        finally:
            dummy.release.set()
        job = poll(client, job_id)
        assert job['status'] == terminal
        assert len(dummy.calls) == 1
        if terminal == 'completed':
            assert job['result'] == {'captured': 2, 'job_id': job_id}
        else:
            assert job['error']['code'] == 'workflow_failed'
            assert 'SECRET' not in str(job)
        assert app.state.store.usage(world)['model_calls']['reserved'] == 0


def test_restart_marks_stale_jobs_interrupted_without_resubmission(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Interrupted question')['id']
    job_id = 'a' * 32
    operation = store.reserve(world, 'search_calls', 'already-sent', {'query': 'test'})
    with store.db(write=True) as db:
        db.execute("INSERT INTO jobs VALUES(?,?,?,'running',NULL,NULL)", (job_id, world, 'collection'))
    instances = []
    def factory(current):
        instances.append(DummyWorkflow(current))
        return instances[-1]
    with TestClient(create_app(tmp_path, workflow_factory=factory)) as client:
        job = client.get('/api/jobs/' + job_id).json()
        assert job['status'] == 'interrupted'
        assert job['error']['code'] == 'process_interrupted'
        assert instances[0].calls == []
        assert store.usage(world)['search_calls']['reserved'] == 1


def test_frozen_research_job_uses_requested_snapshot(application):
    app, _ = application
    with TestClient(app) as client:
        world = client.post('/api/worlds', json={'question': 'Research question'}).json()['id']
        snapshot = client.post(f'/api/worlds/{world}/freeze', json={}).json()['id']
        response = client.post(f'/api/snapshots/{snapshot}/research', json={'model_provider': 'dummy', 'model': 'ok'})
        assert response.status_code == 202
        result = poll(client, response.json()['id'])
        assert result['status'] == 'completed'
        assert result['result']['snapshot_id'] == snapshot
