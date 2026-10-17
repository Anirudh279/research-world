import json
import socket
from web_research_sim.core import Store
from web_research_sim.storage_diagnostics import storage_status


def test_local_check_does_not_create_workspace(tmp_path):
    root = tmp_path / 'absent'
    result = storage_status(root)
    assert result['backend'] == 'local'
    assert result['status'] == 'configured_unverified'
    assert result['remote_requests'] == 0
    assert not root.exists()


def config(tmp_path):
    (tmp_path / 'storage.json').write_text(json.dumps({'backend':'s3','endpoint':'https://private-account.example','bucket':'private-bucket'}))


def test_s3_presence_is_not_connectivity_and_redacts_secrets(tmp_path, monkeypatch):
    config(tmp_path)
    monkeypatch.setattr('web_research_sim.storage_diagnostics.importlib.util.find_spec', lambda _: object())
    monkeypatch.setenv('WRS_S3_ACCESS_KEY_ID','private-access-value')
    monkeypatch.setenv('WRS_S3_SECRET_ACCESS_KEY','private-secret-value')
    def forbidden(*a, **k): raise AssertionError('No connection in diagnostics')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    report = storage_status(tmp_path)
    assert report['status'] == 'configured_unverified'
    assert report['connectivity_verified'] is False
    for value in ('private-account','private-bucket','private-access','private-secret'):
        assert value not in json.dumps(report)


def test_missing_dependency_and_credentials_are_actionable(tmp_path, monkeypatch):
    config(tmp_path)
    monkeypatch.setattr('web_research_sim.storage_diagnostics.importlib.util.find_spec', lambda _: None)
    assert 's3 extra' in storage_status(tmp_path)['detail']
    monkeypatch.setattr('web_research_sim.storage_diagnostics.importlib.util.find_spec', lambda _: object())
    monkeypatch.delenv('WRS_S3_ACCESS_KEY_ID', raising=False)
    assert storage_status(tmp_path)['status'] == 'unavailable'
    assert 'credentials' in storage_status(tmp_path)['detail']


def test_changed_binding_and_malformed_config_fail_closed(tmp_path):
    Store(tmp_path)
    config(tmp_path)
    assert 'differs' in storage_status(tmp_path)['detail']
    (tmp_path/'storage.json').write_text('{"password":"private-secret"}')
    report = storage_status(tmp_path)
    assert report['status'] == 'unavailable'
    assert 'private-secret' not in json.dumps(report)
