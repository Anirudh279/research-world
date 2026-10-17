import json
import socket
import subprocess
from web_research_sim.preflight import preflight


def test_occupied_port_and_missing_docker_do_not_leak_secret(tmp_path, monkeypatch):
    monkeypatch.setattr('web_research_sim.preflight.shutil.which', lambda _: None)
    monkeypatch.setenv('OPENAI_API_KEY', 'FAKE-secret-never-print')
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1',0)); occupied.listen()
        report = preflight(tmp_path, occupied.getsockname()[1])
    checks = {c['name']:c for c in report['checks']}
    assert checks['local_port']['status'] == 'unavailable'
    assert checks['docker']['status'] == 'unavailable'
    assert checks['storage']['status'] == 'ready'
    assert checks['openai']['status'] == 'configured_unverified'
    assert 'FAKE-secret' not in json.dumps(report)
    assert list(tmp_path.iterdir()) == []


def test_timeout_and_missing_storage_are_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr('web_research_sim.preflight.shutil.which', lambda _: '/fake/docker')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('docker',8)
    monkeypatch.setattr('web_research_sim.preflight.subprocess.run', timeout)
    report = preflight(tmp_path/'absent', 49193)
    checks = {c['name']:c for c in report['checks']}
    assert checks['docker']['status'] == 'unavailable'
    assert checks['storage']['status'] == 'unverified'
    assert not (tmp_path/'absent').exists()
    assert report['provider_requests'] == 0


def test_storage_failure_does_not_expose_exception(tmp_path, monkeypatch):
    monkeypatch.setattr('web_research_sim.preflight.shutil.which', lambda _: None)
    def denied(**kwargs):
        raise PermissionError('private mount detail')
    monkeypatch.setattr('web_research_sim.preflight.tempfile.TemporaryFile', denied)
    report = preflight(tmp_path, 49194)
    assert next(c for c in report['checks'] if c['name']=='storage')['status']=='unavailable'
    assert 'private mount' not in json.dumps(report)
