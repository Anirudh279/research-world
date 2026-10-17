from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from web_research_sim.core import Store, Replay, SimError, encoded


def assert_error(code, fn):
    with pytest.raises(SimError) as caught:
        fn()
    assert caught.value.code == code


def test_cutoff_uses_capture_and_archive_times_and_stays_frozen(tmp_path):
    clock = ['2026-01-01T10:00:00+00:00']
    store = Store(tmp_path, clock=lambda: clock[0])
    world = store.create_world('Which source says blue?')['id']
    first = store.add_capture(world, url='https://example.org/a', body=b'blue', text='blue', captured_at=clock[0])
    clock[0] = '2026-01-03T10:00:00+00:00'
    late_archive = store.add_capture(world, url='https://example.org/b', body=b'late', text='blue late', captured_at='2026-01-01T09:00:00+00:00')
    future_capture = store.add_capture(world, url='https://example.org/c', body=b'future', text='blue future', captured_at='2026-01-04T10:00:00+00:00')
    snap = store.freeze(world, '2026-01-02T10:00:00+00:00')
    replay = store.replay(snap['id'])
    assert snap['count'] == 1
    assert replay.open(first['id'])['text'] == 'blue'
    for record in (late_archive, future_capture):
        assert_error('archive_miss', lambda: replay.open(record['id']))
    before = replay.search('blue')
    store.add_capture(world, url='https://example.org/a', body=b'changed', text='changed', captured_at=clock[0])
    assert Store(tmp_path).replay(snap['id']).search('blue') == before
    assert_error('future_cutoff', lambda: store.freeze(world, '2027-01-01T00:00:00Z'))


@pytest.mark.parametrize('field', ['body_sha256', 'text_sha256'])
def test_corrupt_source_or_text_fails_closed(tmp_path, field):
    store = Store(tmp_path)
    world = store.create_world('A question')['id']
    record = store.add_capture(world, url='https://example.org', body=b'original bytes', text='extracted words', captured_at='2020-01-01T00:00:00Z')
    snapshot = store.freeze(world)
    (store.objects.root / record[field]).write_bytes(b'tampered')
    assert_error('integrity_error', lambda: store.replay(snapshot['id']).open(record['id']))
    assert_error('integrity_error', lambda: store.replay(snapshot['id']).search('words'))


def test_manifest_temporal_tampering_and_missing_objects(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('A question')['id']
    record = store.add_capture(world, url='https://example.org', body=b'original', text='text', captured_at='2020-01-01T00:00:00Z')
    snapshot = store.freeze(world)
    manifest = json.loads(store.objects.get(snapshot['id']))
    manifest['records'][0]['archived_at'] = '2099-01-01T00:00:00Z'
    malicious = store.objects.put(encoded(manifest))
    assert_error('temporal_integrity', lambda: Replay(store.objects, malicious))
    (store.objects.root / record['body_sha256']).unlink()
    assert_error('missing_object', lambda: store.replay(snapshot['id']).open(record['id']))


def test_concurrent_budget_reservations_never_overspend_and_failures_stay_charged(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Budget test', {'model_calls': 1, 'search_calls': 5, 'capture_calls': 1})['id']
    def attempt(i):
        try:
            return store.reserve(world, 'search_calls', f'call-{i}', {'query': i})
        except SimError as exc:
            assert exc.code == 'budget_exhausted'
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        admitted = [op for op in pool.map(attempt, range(24)) if op]
    assert len(admitted) == 5
    store.finish(admitted[0], {'error': 'connection uncertain'}, status='uncertain')
    store.finish(admitted[1], {'error': 'provider failed'}, status='failed')
    assert Store(tmp_path).usage(world)['search_calls'] == {'limit': 5, 'reserved': 5}
    assert_error('budget_exhausted', lambda: store.reserve(world, 'search_calls', 'new', {}))
    assert_error('operation_consumed', lambda: store.finish(admitted[0], {'retry': True}))


def test_duplicate_operation_key_admits_one_and_rejects_changed_request(tmp_path):
    store = Store(tmp_path)
    world = store.create_world('Idempotency')['id']
    def attempt(_):
        try:
            return store.reserve(world, 'model_calls', 'same-key', {'prompt': 'one'})
        except SimError as exc:
            assert exc.code == 'operation_consumed'
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len([op for op in pool.map(attempt, range(16)) if op]) == 1
    assert_error('operation_conflict', lambda: store.reserve(world, 'model_calls', 'same-key', {'prompt': 'two'}))
    assert store.usage(world)['model_calls']['reserved'] == 1
