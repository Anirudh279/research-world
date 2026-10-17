"""Response substitution guard; mocked transport, not Docker isolation evidence."""
import json
import pytest
from web_research_sim.core import Store, SimError
from web_research_sim.isolated_replay import IsolatedReplay
from web_research_sim import isolated_replay


def test_well_formed_worker_response_from_other_valid_snapshot_is_rejected(tmp_path,monkeypatch):
    store=Store(tmp_path)
    snapshots=[]
    for text in ('Allowed evidence','Other world evidence'):
        wid=store.create_world(text)['id']
        store.add_capture(wid,url='https://fixture.example',body=text.encode(),text=text,captured_at='2020-01-01T00:00:00Z')
        snapshots.append(store.freeze(wid)['id'])
    intended,foreign=snapshots
    assert store.replay(foreign).search('Other') # foreign manifest is actually valid
    monkeypatch.setattr(isolated_replay,'docker',lambda *a,**k:b'')
    monkeypatch.setattr(IsolatedReplay,'_inspect',lambda *a:None)
    responses=iter([{'snapshot_id':intended,'result':{}},
                    {'snapshot_id':foreign,'result':store.replay(foreign).search('Other')}])
    monkeypatch.setattr(isolated_replay,'_run_worker',lambda *a,**k:json.dumps(next(responses)).encode())
    with IsolatedReplay(store.objects,intended,image_id='sha256:'+'a'*64) as replay:
        staged=replay.root
        assert foreign not in replay.allowed_hashes
        with pytest.raises(SimError) as failure:replay.search('Other')
        assert failure.value.code=='integrity_error'
        assert replay.snapshot_id==intended
        assert all(r['snapshot_id']==intended for r in replay.boundary_receipt)
    assert not staged.exists()
