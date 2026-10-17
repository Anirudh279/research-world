from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading
from web_research_sim.core import Store


def test_waiting_writer_cannot_backdate_archive_availability(tmp_path):
    clock = ['2026-01-01T10:00:00+00:00']
    store = Store(tmp_path, clock=lambda:clock[0])
    world = store.create_world('What was available at the cutoff?')['id']
    original_db = store.db
    waiting = threading.Event()
    @contextmanager
    def observed_db(*,write=False):
        if write:
            waiting.set()
        with original_db(write=write) as db:
            yield db
    store.db = observed_db
    with ThreadPoolExecutor(max_workers=1) as pool:
        # A real SQLite writer holds admission until the cutoff has elapsed.
        with original_db(write=True):
            future = pool.submit(store.add_capture, world, url='https://example.org/late',
                body=b'late fact', text='late fact', captured_at=clock[0],
                headers={'last-modified':'Mon, 01 Jan 2001 00:00:00 GMT'})
            assert waiting.wait(timeout=5)
            clock[0] = '2026-01-01T12:00:00+00:00'
        record = future.result(timeout=5)
    assert record['archived_at'] == clock[0]
    early = store.freeze(world,'2026-01-01T11:00:00+00:00')
    assert early['count'] == 0
    assert store.freeze(world)['count'] == 1
