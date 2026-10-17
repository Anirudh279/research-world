import pytest
from web_research_sim.core import Store,SimError
from web_research_sim.bundles import export_snapshot,import_snapshot


def test_artifacts_roundtrip_and_corruption_fail_closed(tmp_path):
    s=Store(tmp_path/'source');w=s.create_world('Dynamic evidence')['id']
    js=b'document.body.textContent="42"';dom=b'<body>42</body>'
    artifacts=[{'sha256':s.objects.put(body),'bytes':len(body),'kind':kind,'url':'https://example.org/','captured_at':'2026-01-01T00:00:00Z'} for body,kind in [(js,'source_asset'),(dom,'derived_dom')]]
    d=s.add_capture(w,url='https://example.org',body=b'<script src="/a.js"></script>',text='42',captured_at='2026-01-01T00:00:00Z',artifacts=artifacts)
    snap=s.freeze(w)['id'];bundle=export_snapshot(s,snap,redistribution_acknowledged=True)
    dest=Store(tmp_path/'dest');import_snapshot(dest,bundle)
    assert dest.replay(snap).open(d['id'])==s.replay(snap).open(d['id'])
    assert dest.objects.get(artifacts[0]['sha256'])==js
    (dest.objects.root/artifacts[0]['sha256']).write_bytes(b'corrupt')
    with pytest.raises(SimError):dest.replay(snap).open(d['id'])


def test_future_asset_cannot_enter_earlier_snapshot(tmp_path):
    s=Store(tmp_path,clock=lambda:'2026-01-03T00:00:00Z');w=s.create_world('Future asset')['id']
    a={'sha256':s.objects.put(b'future'),'bytes':6,'kind':'source_asset','url':'https://example.org/a','captured_at':'2026-01-04T00:00:00Z'}
    s.add_capture(w,url='https://example.org',body=b'root',text='future',captured_at='2026-01-01T00:00:00Z',artifacts=[a])
    assert s.freeze(w)['count']==0


def test_missing_asset_prevents_capture_admission(tmp_path):
    s=Store(tmp_path);w=s.create_world('Missing asset')['id']
    with pytest.raises(SimError):s.add_capture(w,url='https://example.org',body=b'root',text='text',captured_at='2026-01-01T00:00:00Z',artifacts=[{'sha256':'0'*64,'bytes':1,'kind':'source_asset','url':'https://example.org/a','captured_at':'2026-01-01T00:00:00Z'}])
    assert s.captures(w)==[]


def test_artifact_manifest_requires_new_version(tmp_path):
    from web_research_sim.core import Replay,encoded
    s=Store(tmp_path);w=s.create_world('Versioned artifact')['id'];body=b'asset'
    a={'sha256':s.objects.put(body),'bytes':len(body),'kind':'source_asset','url':'https://example.org/a','captured_at':'2026-01-01T00:00:00Z'}
    s.add_capture(w,url='https://example.org',body=b'root',text='text',captured_at='2026-01-01T00:00:00Z',artifacts=[a])
    snapshot=s.freeze(w)['id'];manifest=s.replay(snapshot).manifest
    assert manifest['schema']=='world-v2'
    manifest['schema']='world-v1'
    with pytest.raises(SimError) as exc:Replay(s.objects,s.objects.put(encoded(manifest)))
    assert exc.value.code=='schema_version'
