"""Portable fixture proofs, not independent clean-install or usability evidence."""
import io
import json
import zipfile
import pytest
from web_research_sim.core import Store, SimError, encoded, digest
from web_research_sim.bundles import export_snapshot, import_snapshot

TIME='2026-01-01T00:00:00+00:00'


@pytest.fixture
def source(tmp_path):
    store=Store(tmp_path/'source',clock=lambda:TIME)
    wid=store.create_world('How much water?')['id']
    doc=store.add_capture(wid,url='https://example.org/water',body=b'<p>Water: 42 cm</p>',text='Water: 42 cm',captured_at=TIME,title='Water')
    store.objects.put(b'PRIVATE_PROVIDER_RAW_SECRET_fixture')
    return store,store.freeze(wid)['id'],doc['id']


def content(source): return export_snapshot(source[0],source[1],redistribution_acknowledged=True)


def members(bundle):
    with zipfile.ZipFile(io.BytesIO(bundle)) as z: return {n:z.read(n) for n in z.namelist()}


def zipfiles(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,body in files.items(): z.writestr(name,body)
    return out.getvalue()


def test_exact_snapshot_100_calls_reset_restart_and_import(source,tmp_path):
    store,sid,did=source
    bundle=content(source)
    assert bundle==content(source)
    target=Store(tmp_path/'target',clock=lambda:TIME)
    receipt=import_snapshot(target,bundle)
    assert receipt['id']==sid
    restarted=Store(tmp_path/'target',clock=lambda:TIME)
    for i in range(100):
        before=store.replay(sid)
        after=restarted.replay(sid)
        action=(lambda r:r.search('water')) if i%3==0 else (lambda r:r.open(did)) if i%3==1 else (lambda r:r.find(did,'42'))
        assert digest(encoded(action(before)))==digest(encoded(action(after)))
    assert import_snapshot(target,bundle)==receipt
    assert not target.captures(receipt['world_id'])  # Frozen imported evidence is not a new live capture.
    assert all(v['limit']==0 for v in target.usage(receipt['world_id']).values())


def test_export_needs_rights_and_excludes_unrelated_objects(source):
    with pytest.raises(SimError) as exc: export_snapshot(source[0],source[1])
    assert exc.value.code=='redistribution_required'
    bundle=content(source)
    assert b'PRIVATE_PROVIDER_RAW_SECRET_fixture' not in bundle
    assert 'state.sqlite3' not in members(bundle)


def test_metadata_only_explicitly_nonreproducible(source,tmp_path):
    bundle=export_snapshot(source[0],source[1],metadata_only=True)
    files=members(bundle)
    assert list(files)==['challenge.json']
    descriptor=json.loads(files['challenge.json'])
    assert descriptor['reproducible'] is False
    with pytest.raises(SimError) as exc: import_snapshot(Store(tmp_path/'dest'),bundle)
    assert exc.value.code=='non_reproducible_bundle'


@pytest.mark.parametrize('path',['../escape','/tmp/escape','objects/../../escape','objects/'+'a'*64+'/extra','state.sqlite3'])
def test_arbitrary_paths_rejected_before_write(source,tmp_path,path):
    files=members(content(source)); files[path]=b'evil'
    target=Store(tmp_path/'target')
    with pytest.raises(SimError): import_snapshot(target,zipfiles(files))
    assert not target.worlds()


def test_corrupt_missing_and_unreferenced_objects_rejected(source,tmp_path):
    original=members(content(source))
    object_name=next(k for k in original if k.startswith('objects/') and k!='objects/'+source[1])
    for variant in ('corrupt','missing','extra'):
        files=dict(original)
        if variant=='corrupt': files[object_name]=b'corrupt'
        elif variant=='missing': del files[object_name]
        else: files['objects/'+digest(b'extra')]=b'extra'
        target=Store(tmp_path/variant)
        with pytest.raises(SimError): import_snapshot(target,zipfiles(files))
        assert not target.worlds()


def test_future_record_rejected_even_with_recomputed_manifest_hash(source,tmp_path):
    files=members(content(source))
    m=json.loads(files.pop('objects/'+source[1]))
    m['records'][0]['captured_at']='2027-01-01T00:00:00+00:00'
    raw=encoded(m); sid=digest(raw)
    files['objects/'+sid]=raw
    descriptor=json.loads(files['challenge.json']); descriptor['snapshot_id']=sid
    files['challenge.json']=encoded(descriptor)
    target=Store(tmp_path/'target')
    with pytest.raises(SimError) as exc: import_snapshot(target,zipfiles(files))
    assert exc.value.code=='temporal_integrity' and not target.worlds()


def test_duplicate_entry_rejected(source,tmp_path):
    out=io.BytesIO(content(source))
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(out,'a') as z: z.writestr('challenge.json',b'{}')
    with pytest.raises(SimError): import_snapshot(Store(tmp_path/'target'),out.getvalue())


def test_existing_world_conflict_preserved(source,tmp_path):
    target=Store(tmp_path/'target')
    wid=source[0].replay(source[1]).manifest['world_id']
    with target.db(write=True) as db:
        db.execute('INSERT INTO worlds VALUES(?,?,?,?)',(wid,'Conflicting question',TIME,encoded({'model_calls':1,'capture_calls':1,'search_calls':1}).decode()))
    with pytest.raises(SimError) as exc: import_snapshot(target,content(source))
    assert exc.value.code=='world_conflict'
    assert target.world(wid)['question']=='Conflicting question'
    with target.db() as db: assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0]==0


def test_symlink_member_rejected(tmp_path):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        i=zipfile.ZipInfo('challenge.json'); i.external_attr=0o120777<<16
        z.writestr(i,b'/etc/passwd')
    with pytest.raises(SimError): import_snapshot(Store(tmp_path/'target'),out.getvalue())


def test_entry_and_total_limits_before_import(source,tmp_path,monkeypatch):
    from web_research_sim import bundles
    bundle=content(source)
    monkeypatch.setattr(bundles,'MAX_ENTRY',10)
    with pytest.raises(SimError): import_snapshot(Store(tmp_path/'small-entry'),bundle)
    monkeypatch.setattr(bundles,'MAX_ENTRY',16*1024**2)
    monkeypatch.setattr(bundles,'MAX_TOTAL',10)
    with pytest.raises(SimError): import_snapshot(Store(tmp_path/'small-total'),bundle)


def test_compressed_input_explicitly_rejected(source,tmp_path):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,body in members(content(source)).items(): z.writestr(name,body)
    with pytest.raises(SimError): import_snapshot(Store(tmp_path/'target'),out.getvalue())
