"""Real local API scheduler loop, controlled workflow; no paid calls."""
from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi.testclient import TestClient
import pytest
from web_research_sim.api import create_app
from web_research_sim.core import Store
from web_research_sim.scheduler import ScheduleManager


class ControlledWorkflow:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.called = threading.Event()
    def collect(self, world, job, **config):
        self.calls.append((world, job, config))
        self.called.set()
        return {'controlled_fixture': True}
    def capture_discovered(self, world, job, **config):
        return self.collect(world, job, **config)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN', raising=False)
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)


def config(start=None, key='one'):
    return {'kind': 'collection', 'config': {'model_provider': 'test', 'model': 'test', 'search_provider': 'test'},
            'interval_seconds': 60, 'max_runs': 1, 'start_at': (start or datetime.now(timezone.utc)).isoformat(), 'request_id': key}


def test_background_scheduler_finite_execution_and_idempotency(tmp_path):
    instances = []
    def factory(store):
        instances.append(ControlledWorkflow(store))
        return instances[-1]
    app = create_app(tmp_path, workflow_factory=factory, scheduler_poll_seconds=.02)
    with TestClient(app) as client:
        world = client.post('/api/worlds', json={'question': 'Scheduled fixture'}).json()['id']
        request = config()
        first = client.post(f'/api/worlds/{world}/schedules', json=request)
        assert first.status_code == 201, first.text
        sid = first.json()['id']
        assert client.post(f'/api/worlds/{world}/schedules', json=request).json()['id'] == sid
        assert instances[0].called.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = client.get('/api/schedules/' + sid).json()
            if status['status'] == 'completed': break
            time.sleep(.01)
        assert status['status'] == 'completed'
        assert len(status['slots']) == 1
        assert status['slots'][0]['status'] == 'completed'
        assert len(instances[0].calls) == 1
        assert client.post(f'/api/worlds/{world}/schedules', json={**request, 'max_runs': 2}).status_code == 409
        listing = client.get('/api/schedules', params={'world_id': world}).json()
        assert listing['runner']['status'] == 'running'
        assert len(listing['schedules']) == 1
    assert app.state.scheduler_state['status'] == 'stopped'


def test_future_schedule_pause_resume_and_capture_only_config(tmp_path):
    app = create_app(tmp_path, workflow_factory=ControlledWorkflow, scheduler_poll_seconds=.02)
    with TestClient(app) as client:
        world = client.post('/api/worlds', json={'question': 'Future fixture'}).json()['id']
        request = config(datetime.now(timezone.utc) + timedelta(days=1))
        request.update(kind='native_discovered', config={'sources': [{'url': 'https://example.org/fixture', 'query': 'fixture'}], 'native_client': 'test-declared'})
        response = client.post(f'/api/worlds/{world}/schedules', json=request)
        assert response.status_code == 201, response.text
        sid = response.json()['id']
        assert client.post(f'/api/schedules/{sid}/pause', json={}).json()['status'] == 'paused'
        assert client.post(f'/api/schedules/{sid}/resume', json={}).json()['status'] == 'active'
        assert client.get(f'/api/schedules/{sid}').json()['slots'] == []
        assert client.post(f'/api/schedules/{sid}/pause', json={'cutoff': 'unexpected'}).status_code == 422
        assert client.post(f'/api/worlds/{world}/schedules', json={**request, 'max_runs': 0}).status_code == 422


def test_startup_recovery_only_under_api_lock_and_owner_namespace(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Recovery fixture')['id']
    api_manager = ScheduleManager(store)
    external = ScheduleManager(store, owner_id='external-soak-owner')
    start = datetime.now(timezone.utc)
    a = api_manager.create(world, **config(start, 'api'))
    b = external.create(world, **config(start, 'external'))
    with store.db(write=True) as db:
        for schedule, owner, job in [(a, 'api-scheduler', 'a'*32), (b, 'external-soak-owner', 'b'*32)]:
            db.execute('INSERT INTO schedule_slots VALUES(?,?,?,?,?,?,NULL)', (schedule['id'], 0, start.isoformat(), job, owner, 'claimed'))
            db.execute('UPDATE schedules SET next_index=1 WHERE id=?', (schedule['id'],))
    app = create_app(tmp_path, workflow_factory=ControlledWorkflow, scheduler_poll_seconds=.02)
    with TestClient(app) as client:
        assert client.get('/api/schedules/' + a['id']).json()['slots'][0]['status'] == 'interrupted'
        assert external.status(b['id'])['slots'][0]['status'] == 'claimed'
        assert client.post('/api/schedules/' + b['id'] + '/pause', json={}).status_code == 409
        second = create_app(tmp_path, workflow_factory=ControlledWorkflow)
        with pytest.raises(RuntimeError, match='Another server owns'):
            with TestClient(second):
                pass
        assert external.status(b['id'])['slots'][0]['status'] == 'claimed'


def test_cli_schedule_payload(tmp_path, monkeypatch):
    import json
    import httpx
    from web_research_sim import cli
    from web_research_sim.client import Client
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'id': 'scheduled', 'status': 'active'})
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: Client('http://test', transport=httpx.MockTransport(handle)))
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config()['config']))
    assert cli.main(['schedule','create','world','--kind','collection','--config',str(path),'--interval-seconds','60','--max-runs','2','--start-at','2026-11-01T00:00:00+00:00','--request-key','stable']) == 0
    body = json.loads(seen[-1].content)
    assert body['request_id'] == 'stable' and body['max_runs'] == 2
    assert cli.main(['schedule','pause','scheduled']) == 0
    assert seen[-1].url.path == '/api/schedules/scheduled/pause'
