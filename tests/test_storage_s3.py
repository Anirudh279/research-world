"""Actual loopback Moto HTTP emulator, not an AWS/R2 service qualification."""
import io
import json
import zipfile
import pytest
pytest.importorskip('moto.server')
from moto.server import ThreadedMotoServer
from web_research_sim.core import Store,SimError,digest
from web_research_sim.storage import S3Objects,storage_config
from web_research_sim.bundles import export_snapshot,import_snapshot

@pytest.fixture
def cloud(tmp_path,monkeypatch):
    server=ThreadedMotoServer(ip_address='127.0.0.1',port=0,verbose=False);server.start()
    host,port=server.get_host_and_port()
    config={'backend':'s3','endpoint':f'http://{host}:{port}','bucket':'fixture-archive','prefix':'objects','region':'us-east-1'}
    monkeypatch.setenv('WRS_S3_ACCESS_KEY_ID','fixture-key')
    monkeypatch.setenv('WRS_S3_SECRET_ACCESS_KEY','fixture-secret')
    (tmp_path/'storage.json').write_text(json.dumps(config))
    objects=S3Objects(config);objects.client.create_bucket(Bucket=config['bucket'])
    try:yield tmp_path,config,objects,server
    finally:server.stop()


def test_conditional_immutable_and_corrupt_collision(cloud):
    root,config,obj,_=cloud
    sha=obj.put(b'original');assert obj.put(b'original')==sha
    obj.client.put_object(Bucket=config['bucket'],Key='objects/'+sha,Body=b'corrupt')
    with pytest.raises(SimError,match='digest'):obj.put(b'original')
    assert obj.client.get_object(Bucket=config['bucket'],Key='objects/'+sha)['Body'].read()==b'corrupt'
    with pytest.raises(SimError,match='missing'):obj.get('0'*64)
    with pytest.raises(SimError,match='16 MiB'):obj.put(b'x'*(16*1024**2+1))


def test_actual_http_snapshot_export_import_restart_and_no_fallback(cloud,tmp_path):
    root,config,obj,server=cloud
    store=Store(root);world=store.create_world('Fixture question')['id']
    doc=store.add_capture(world,url='https://example.org/',body=b'<p>42</p>',text='42',captured_at='2026-01-01T00:00:00Z')
    sid=store.freeze(world)['id']
    assert Store(root).replay(sid).open(doc['id'])['text']=='42'
    bundle=export_snapshot(store,sid,redistribution_acknowledged=True)
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        assert 'storage.json' not in z.namelist()
        assert all(b'fixture-secret' not in z.read(n) for n in z.namelist())
    target=Store(root/'local-target');assert import_snapshot(target,bundle)['id']==sid
    assert target.replay(sid).open(doc['id'])['text']=='42'
    # Local copy deliberately exists; missing remote content must still fail closed.
    (root/'objects').mkdir();(root/'objects'/sid).write_bytes(obj.get(sid))
    obj.client.delete_object(Bucket=config['bucket'],Key='objects/'+sid)
    with pytest.raises(SimError,match='missing'):store.replay(sid)
    (root/'storage.json').unlink()
    with pytest.raises(SimError,match='selection changed'):Store(root)


def test_credentials_explicit_and_config_secret_rejected(cloud,monkeypatch):
    root,config,obj,_=cloud
    monkeypatch.delenv('WRS_S3_SECRET_ACCESS_KEY')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','ambient-not-allowed')
    with pytest.raises(SimError,match='dedicated'):S3Objects(config)
    (root/'storage.json').write_text(json.dumps({**config,'secret':'bad'}))
    with pytest.raises(SimError,match='non-secret'):storage_config(root)


def test_local_default_and_existing_migration_rejected(tmp_path):
    store=Store(tmp_path);store.create_world('Local')
    (tmp_path/'storage.json').write_text(json.dumps({'backend':'s3','endpoint':'https://example.org','bucket':'fixture-bucket'}))
    with pytest.raises(SimError,match='selection changed'):Store(tmp_path)


def test_service_error_redacted_no_retry():
    class Broken:
        count=0
        def put_object(self,**kwargs):self.count+=1;raise RuntimeError('SECRET endpoint')
    fake=Broken();obj=S3Objects({'bucket':'bucket','prefix':'objects'},client=fake)
    with pytest.raises(SimError,match='uncertain') as exc:obj.put(b'x')
    assert 'SECRET' not in str(exc.value) and fake.count==1


def test_actual_emulator_outage_fails_closed(cloud):
    root,config,obj,server=cloud
    sha=obj.put(b'offline');server.stop()
    with pytest.raises(SimError,match='no fallback'):obj.get(sha)


def test_oversized_remote_rejected_before_read_and_closed():
    class Body:
        closed=False
        def read(self,n):raise AssertionError('must not read oversized object')
        def close(self):self.closed=True
    body=Body()
    class Client:
        def get_object(self,**kwargs):return {'Body':body,'ContentLength':16*1024**2+1}
    obj=S3Objects({'bucket':'bucket','prefix':'objects'},client=Client())
    with pytest.raises(SimError,match='size limit'):obj.get('0'*64)
    assert body.closed


def test_capture_worker_forwards_only_dedicated_s3_credentials(cloud,monkeypatch):
    from web_research_sim import bounded_capture as b
    import sys
    root,config,obj,_=cloud;store=Store(root);wid=store.create_world('Child S3 fixture')['id']
    runner=b._run_worker
    script='''
from web_research_sim import capture_worker as w
from web_research_sim.capture import CapturedPage
import os
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
assert os.environ['WRS_S3_SECRET_ACCESS_KEY']=='fixture-secret'
def fake(url,max_bytes,original_sink):
    raw=b'child original';at='2026-01-01T00:00:00Z'
    receipt=original_sink(raw,{'url':url,'requested_url':url,'status':200,'captured_at':at})
    return CapturedPage(url,url,raw,'child original','',200,{},at,[],original_response_id=receipt)
w.fetch_page=fake
w.main()
'''
    def controlled(command,request,**kwargs):return runner([sys.executable,'-c',script],request,**kwargs)
    monkeypatch.setattr(b,'_run_worker',controlled)
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','ambient-never-forward')
    page=b.fetch_bounded(store,wid,'https://example.org/',timeout_seconds=10)
    with store.db() as db:row=db.execute('SELECT * FROM original_responses WHERE id=?',(page.original_response_id,)).fetchone()
    assert obj.get(row['body_hash'])==b'child original'
