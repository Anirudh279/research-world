import hashlib
import json
from pathlib import Path
import pytest
from web_research_sim.core import Store
from web_research_sim.starters import list_starters,install_starter,CATALOG,T1,T2,T3
from web_research_sim.bundles import export_snapshot


@pytest.mark.parametrize('slug',['river','energy','civic'])
def test_three_temporal_slices_and_missing_source(slug,tmp_path):
    store=Store(tmp_path)
    installed=install_starter(store,slug)
    assert installed['synthetic'] is True and 'CC0' in installed['source_license']
    assert [s['count'] for s in installed['snapshots']]==[2,3,4]
    for snapshot in installed['snapshots']:
        replay=store.replay(snapshot['id'])
        assert all(r['captured_at']<=snapshot['cutoff'] and r['archived_at']<=snapshot['cutoff'] for r in replay.records.values())
        assert all(r['provenance']['kind']=='self_authored_synthetic' for r in replay.records.values())
        assert CATALOG[slug]['missing_url'] not in {r['url'] for r in replay.records.values()}
    default=store.replay(installed['default_snapshot_id'])
    assert default.manifest['cutoff']==T2
    future_text=next(p[3] for p in CATALOG[slug]['pages'] if p[1]==T3)
    assert future_text not in [default.open(d)['text'] for d in default.records]
    assert all(v['reserved']==0 for v in store.usage(installed['world_id']).values())


def test_evaluator_labels_frozen_and_not_in_agent_objects(tmp_path):
    root=Path(__file__).resolve().parents[1]
    references=root/'evaluation/starter-reference-tasks-v1.json'
    raw=references.read_bytes(); receipt=json.loads((root/'evaluation/starter-reference-tasks-v1.freeze.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==receipt['sha256'] and len(raw)==receipt['bytes']
    tasks=json.loads(raw)
    assert len(tasks['tasks'])==12 and tasks['label_review_status']=='unreviewed'
    assert {t['label'] for t in tasks['tasks']}=={'correct','unsupported','contradicted','temporally_invalid','incomplete'}
    assert len({t['id'] for t in tasks['tasks']})==12
    store=Store(tmp_path)
    for slug in CATALOG:
        installed=install_starter(store,slug)
        bundle=export_snapshot(store,installed['default_snapshot_id'],redistribution_acknowledged=True)
        assert raw not in bundle
        assert b'reference_items' not in bundle and b'label_review_status' not in bundle
        for task in tasks['tasks']:
            assert task['id'].encode() not in bundle
    assert not list((root/'src').rglob('starter-reference-tasks*'))


def test_catalog_is_explicitly_synthetic_and_install_does_not_change_clock(tmp_path):
    fixed=lambda:'2026-10-15T12:00:00+00:00'
    store=Store(tmp_path,clock=fixed)
    assert len(list_starters())==3 and all(x['synthetic'] for x in list_starters())
    install_starter(store,'river')
    assert store.clock is fixed and store.clock()=='2026-10-15T12:00:00+00:00'


def test_frozen_evidence_references_resolve_in_default_worlds(tmp_path):
    root=Path(__file__).resolve().parents[1]
    path=root/'evaluation/starter-evidence-references-v1.json'
    raw=path.read_bytes(); receipt=json.loads(path.with_name('starter-evidence-references-v1.freeze.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest()==receipt['sha256']
    store=Store(tmp_path)
    replays={slug:store.replay(install_starter(store,slug)['default_snapshot_id']) for slug in CATALOG}
    references=json.loads(raw)['tasks']
    assert len(references)==12
    for task in references:
        replay=replays[task['starter']]
        for ref in task['references']:
            assert any(ref['quote'] in replay.open(r['id'])['text'] for r in replay.records.values() if r['url']==ref['url'])
