"""Durable API admission tests. No real search/model/capture requests."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from fastapi.testclient import TestClient
from web_research_sim.api import create_app


class ControlledWorkflow:
    def __init__(self, store):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def collect(self, world, job, **kwargs):
        self.calls.append((world, job, kwargs))
        self.started.set()
        assert self.release.wait(5)
        return {'test_only': True}

    def research(self, snapshot, job, **kwargs):
        return self.collect(snapshot, job, **kwargs)


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    monkeypatch.delenv('WR_SIM_TOKEN', raising=False)
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)


def make(tmp_path):
    instances = []
    def factory(store):
        instances.append(ControlledWorkflow(store))
        return instances[-1]
    app = create_app(tmp_path, workflow_factory=factory)
    return app, instances[0]


def route(client, kind):
    world = client.post('/api/worlds', json={'question': 'Admission fixture'}).json()['id']
    if kind == 'collect':
        return f'/api/worlds/{world}/collect', {'model_provider': 'test', 'model': 'test', 'search_provider': 'test'}
    snapshot = client.post(f'/api/worlds/{world}/freeze', json={}).json()['id']
    return f'/api/snapshots/{snapshot}/research', {'model_provider': 'test', 'model': 'test'}


@pytest.mark.parametrize('kind', ['collect', 'research'])
def test_concurrent_replay_dispatches_once(tmp_path, kind):
    app, workflow = make(tmp_path)
    with TestClient(app) as client:
        path, body = route(client, kind)
        try:
            with ThreadPoolExecutor(max_workers=8) as callers:
                results = list(callers.map(lambda _: client.post(path, json=body, headers={'Idempotency-Key': 'same-request'}), range(8)))
            assert all(r.status_code == 202 for r in results)
            assert len({r.json()['id'] for r in results}) == 1
            assert sum(bool(r.json().get('replayed')) for r in results) == 7
            assert workflow.started.wait(3)
            repeated = client.post(path, json=body, headers={'Idempotency-Key': 'same-request'}).json()
            assert repeated['status'] == 'running'
            assert len(workflow.calls) == 1
            with app.state.store.db() as db:
                assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
                assert db.execute('SELECT COUNT(*) FROM job_request_keys').fetchone()[0] == 1
        finally:
            workflow.release.set()


@pytest.mark.parametrize('kind', ['collect', 'research'])
def test_conflict_rejects_changed_body_and_target(tmp_path, kind):
    app, workflow = make(tmp_path)
    with TestClient(app) as client:
        path, body = route(client, kind)
        try:
            first = client.post(path, json=body, headers={'Idempotency-Key': 'fixed'})
            assert first.status_code == 202
            changed = client.post(path, json={**body, 'model': 'another'}, headers={'Idempotency-Key': 'fixed'})
            assert changed.status_code == 409
            assert changed.json()['error']['code'] == 'idempotency_conflict'
            other_path, _ = route(client, kind)
            assert client.post(other_path, json=body, headers={'Idempotency-Key': 'fixed'}).status_code == 409
            # Explicit defaults and omitted defaults bind to the same request.
            defaults = {'max_queries': 3, 'max_pages': 5} if kind == 'collect' else {'max_steps': 8}
            assert client.post(path, json={**body, **defaults}, headers={'Idempotency-Key': 'fixed'}).json()['id'] == first.json()['id']
        finally:
            workflow.release.set()


@pytest.mark.parametrize('status', ['completed', 'failed', 'running', 'queued'])
def test_restart_observes_consumed_request_never_dispatches(tmp_path, status):
    app, workflow = make(tmp_path)
    workflow.release.set()
    with TestClient(app) as client:
        path, body = route(client, 'collect')
        first = client.post(path, json=body, headers={'Idempotency-Key': 'restart'}).json()
    # Simulate persisted state at process loss after admission. Do not actually
    # replay the synthetic provider request when the app reopens the database.
    with app.state.store.db(write=True) as db:
        db.execute('UPDATE jobs SET status=? WHERE id=?', (status, first['id']))
    restarted, new_workflow = make(tmp_path)
    with TestClient(restarted) as client:
        observed = client.post(path, json=body, headers={'Idempotency-Key': 'restart'}).json()
        assert observed['id'] == first['id']
        assert observed['status'] == ('interrupted' if status in {'queued', 'running'} else status)
        assert observed['replayed'] is True
        assert new_workflow.calls == []


def test_dispatch_uncertainty_stays_consumed(tmp_path, monkeypatch):
    import web_research_sim.api as api
    class UncertainExecutor:
        count = 0
        def __init__(self, **kwargs):
            pass
        def submit(self, fn):
            self.count += 1
            raise RuntimeError('uncertain dispatch')
        def shutdown(self, **kwargs):
            pass
    monkeypatch.setattr(api, 'ThreadPoolExecutor', UncertainExecutor)
    app, workflow = make(tmp_path)
    with TestClient(app) as client:
        path, body = route(client, 'collect')
        first = client.post(path, json=body, headers={'Idempotency-Key': 'uncertain'}).json()
        assert first['status'] == 'interrupted'
        observed = client.post(path, json=body, headers={'Idempotency-Key': 'uncertain'}).json()
        assert observed['id'] == first['id']
        assert observed['status'] == 'interrupted'
        assert observed['replayed'] is True
        assert client.get('/api/jobs/' + first['id']).json()['error']['code'] == 'dispatch_uncertain'
        assert workflow.calls == []


@pytest.mark.parametrize('key', ['', 'bad key', 'x' * 129])
def test_invalid_keys_do_not_admit_jobs(tmp_path, key):
    app, workflow = make(tmp_path)
    with TestClient(app) as client:
        path, body = route(client, 'collect')
        assert client.post(path, json=body, headers={'Idempotency-Key': key}).status_code == 400
        with app.state.store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_no_key_preserves_explicit_new_job_behavior(tmp_path):
    app, workflow = make(tmp_path)
    workflow.release.set()
    with TestClient(app) as client:
        path, body = route(client, 'collect')
        first = client.post(path, json=body).json()
        second = client.post(path, json=body).json()
        assert first['id'] != second['id']
