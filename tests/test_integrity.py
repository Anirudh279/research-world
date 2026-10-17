import json
import pytest
from web_research_sim.core import Store, encoded
from web_research_sim.integrity import verify_snapshot


def fixture(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Verify evidence')['id']
    rows = [store.add_capture(world, url=f'https://example.org/{i}', body=f'original {i}'.encode(), text=f'text {i}', captured_at='2020-01-01T00:00:00Z') for i in range(3)]
    return store, rows, store.freeze(world)['id']


def test_all_records_checked_despite_corruption_and_missing(tmp_path):
    store, rows, snapshot = fixture(tmp_path)
    assert verify_snapshot(store.objects, snapshot)['passed']
    (store.objects.root / rows[0]['body_sha256']).write_bytes(b'corrupt')
    (store.objects.root / rows[1]['text_sha256']).unlink()
    result = verify_snapshot(store.objects, snapshot)
    assert not result['passed']
    assert result['documents_checked'] == 3
    assert result['documents_passed'] == 1
    assert {f['code'] for f in result['failures']} == {'integrity_error', 'missing_object'}
    assert result['referenced_objects'] == 7


def test_temporal_manifest_failure(tmp_path):
    store, rows, snapshot = fixture(tmp_path)
    manifest = json.loads(store.objects.get(snapshot))
    manifest['records'][0]['archived_at'] = '2099-01-01T00:00:00Z'
    result = verify_snapshot(store.objects, store.objects.put(encoded(manifest)))
    assert not result['passed']
    assert result['failures'] == [{'scope': 'manifest', 'code': 'temporal_integrity'}]


def test_cli_exit_codes(tmp_path, capsys):
    from web_research_sim.cli import main
    store, rows, snapshot = fixture(tmp_path)
    assert main(['verify-world', snapshot, '--data', str(tmp_path)]) == 0
    (store.objects.root / rows[-1]['body_sha256']).unlink()
    assert main(['verify-world', snapshot, '--data', str(tmp_path)]) == 1
    assert 'missing_object' in capsys.readouterr().out


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{}', b'not json'])
def test_malformed_manifest_is_failed_receipt(tmp_path, raw):
    store = Store(tmp_path)
    result = verify_snapshot(store.objects, store.objects.put(raw))
    assert not result['passed']
    assert result['failures'][0]['scope'] == 'manifest'


def test_corrupt_asset_is_reported(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Asset integrity')['id']
    sha = store.objects.put(b'asset')
    store.add_capture(world, url='https://example.org', body=b'original', text='text', captured_at='2020-01-01T00:00:00Z', artifacts=[{'sha256':sha,'bytes':5,'kind':'source_asset','url':'https://example.org/a','captured_at':'2020-01-01T00:00:00Z'}])
    snapshot = store.freeze(world)['id']
    (store.objects.root / sha).unlink()
    result = verify_snapshot(store.objects, snapshot)
    assert not result['passed']
    assert result['failures'][0]['code'] == 'missing_object'
