import json
import os
from pathlib import Path
import subprocess
import time
import pytest
from web_research_sim.capture import CaptureError
from web_research_sim.isolated_renderer import render_isolated,validate_boundary,_bounded

ROOT='https://research.test/'


@pytest.fixture
def image():
    value=os.environ.get('RENDER_TEST_IMAGE')
    if not value: pytest.skip('Explicit immutable Docker renderer image required')
    return value


def boundary():
    return {'Id':'fixture','Image':'sha256:'+'a'*64,'State':{'Running':True},'Mounts':[],
        'Config':{'User':'pwuser','Image':'sha256:'+'a'*64},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'NanoCpus':2_000_000_000,'Memory':1024**3,'MemorySwap':1024**3,'PidsLimit':128,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Tmpfs':{'/tmp':'rw,nosuid,nodev,size=268435456'}}}


@pytest.mark.parametrize('field,value',[('NetworkMode','bridge'),('ReadonlyRootfs',False),('Memory',0),('PidsLimit',0),('CapDrop',[]),('Binds',['/var/run/docker.sock:/var/run/docker.sock']),('PidMode','host')])
def test_boundary_rejects_mismatch(field,value):
    data=boundary();data['HostConfig'][field]=value
    with pytest.raises(CaptureError,match='boundary_mismatch'):validate_boundary(data,data['Image'])


def test_callback_deadline_bounds_blocking_sink(tmp_path):
    pidfile=tmp_path/'child.pid'
    def sink(*args):
        pidfile.write_text(str(os.getpid()));time.sleep(30)
    started=time.monotonic()
    with pytest.raises(CaptureError,match='callback_deadline'):
        _bounded('save',None,sink,None,0,time.monotonic()+.3,{'body':b'x','metadata':{}})
    assert time.monotonic()-started<2
    pid=int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):os.kill(pid,0)


def fixtures(tmp_path,*,bad=False):
    root=b'<html><body><p id="value">pending</p><script src="/app.js"></script></body></html>'
    script=b'fetch("/data.json").then(r=>r.json()).then(x=>document.querySelector("#value").textContent="Level "+x.level)'
    if bad:script+=b';fetch("/redirect").catch(()=>{});new WebSocket("wss://research.test/socket");'
    resources={ROOT:(root,'text/html'),ROOT+'app.js':(script,'application/javascript'),ROOT+'data.json':(b'{"level":47}','application/json')}
    def fetch(url,**limits):
        if url==ROOT+'redirect':return {'url':url,'body':b'','status':302,'headers':{'location':'http://169.254.169.254/latest/meta-data/'},'captured_at':'2026-01-01T00:00:00+00:00'}
        if url not in resources:raise CaptureError('fixture_resource_missing')
        body,kind=resources[url]
        return {'url':url,'body':body,'status':200,'headers':{'content-type':kind},'captured_at':'2026-01-01T00:00:00+00:00'}
    def sink(body,metadata):
        import hashlib
        sha=hashlib.sha256(body).hexdigest();(tmp_path/sha).write_bytes(body)
        with (tmp_path/'receipts.jsonl').open('a') as f:f.write(json.dumps(metadata)+'\n')
        return sha
    return fetch,sink


def test_real_offline_docker_browser_preserves_assets_and_cleans(image,tmp_path):
    fetch,sink=fixtures(tmp_path)
    result=render_isolated(ROOT,image_id=image,fetch_response=fetch,original_sink=sink,enabled=True)
    assert result.text=='Level 47' and result.boundary['network']=='none' and result.boundary['host_mounts']==0
    assert result.boundary['image_id']==image and len(result.assets)==3
    receipts=[json.loads(s) for s in (tmp_path/'receipts.jsonl').read_text().splitlines()]
    assert [r['kind'] for r in receipts]==['original_before_render']*3+['derived_rendered_dom']
    assert all((tmp_path/a['sha256']).exists() for a in result.assets)
    assert subprocess.run(['docker','inspect',result.boundary['container_id']],capture_output=True).returncode!=0


def test_real_offline_redirect_private_and_websocket_blocked(image,tmp_path):
    fetch,sink=fixtures(tmp_path,bad=True)
    result=render_isolated(ROOT,image_id=image,fetch_response=fetch,original_sink=sink,enabled=True)
    assert result.text=='Level 47' and result.status=='partial'
    assert {r['reason'] for r in result.blocked_requests}>={'private_destination','websocket_disabled'}
    assert any(a['status']==302 for a in result.assets)


def test_real_offline_browser_blocking_fetch_deadline(image,tmp_path):
    fetch,sink=fixtures(tmp_path)
    def slow(*a,**kw):time.sleep(30)
    started=time.monotonic()
    with pytest.raises(CaptureError,match='callback_deadline|wallclock_limit'):
        render_isolated(ROOT,image_id=image,fetch_response=slow,original_sink=sink,enabled=True,max_seconds=8)
    assert time.monotonic()-started<20


def test_encoded_original_persisted_before_reject(tmp_path):
    def fetch(url,**kwargs):
        assert kwargs['timeout_seconds']<=20
        return {'url':url,'body':b'encoded-body','status':200,'headers':{'content-encoding':'gzip'},'captured_at':'2026-01-01T00:00:00+00:00'}
    def sink(body,meta): (tmp_path/'body').write_bytes(body);return 'saved'
    result=_bounded('fetch',fetch,sink,ROOT,100,time.monotonic()+40)
    assert not result['ok'] and result['error']=='encoded_response_unsupported'
    assert (tmp_path/'body').read_bytes()==b'encoded-body' and result['asset']['receipt']=='saved'


def test_real_offline_public_redirect_binds_final_original(image,tmp_path):
    import hashlib
    def fetch(url,**kwargs):
        if url==ROOT:return {'url':url,'body':b'initial redirect','status':302,'headers':{'location':ROOT+'final'},'captured_at':'2026-01-01T00:00:00+00:00'}
        if url==ROOT+'final':return {'url':url,'body':b'<html><body>Final original</body></html>','status':200,'headers':{'content-type':'text/html'},'captured_at':'2026-01-01T00:00:00+00:00'}
        raise CaptureError('fixture_resource_missing')
    def sink(body,meta):
        sha=hashlib.sha256(body).hexdigest();(tmp_path/sha).write_bytes(body);return sha
    result=render_isolated(ROOT,image_id=image,fetch_response=fetch,original_sink=sink,enabled=True)
    assert result.url==ROOT+'final' and result.requested_url==ROOT
    assert result.source_sha256==hashlib.sha256(b'<html><body>Final original</body></html>').hexdigest()
    assert any(a['status']==302 and a['url']==ROOT for a in result.assets)


def test_actual_boundary_failure_precedes_any_fetch_or_sink(image,tmp_path,monkeypatch):
    from web_research_sim import isolated_renderer
    marker=tmp_path/'called'
    def forbidden(*args,**kwargs):marker.write_text('called');raise AssertionError('Must not feed bytes before inspected boundary')
    def reject(*args):raise CaptureError('renderer_boundary_mismatch')
    monkeypatch.setattr(isolated_renderer,'validate_boundary',reject)
    with pytest.raises(CaptureError,match='boundary_mismatch'):
        render_isolated(ROOT,image_id=image,fetch_response=forbidden,original_sink=forbidden,enabled=True)
    assert not marker.exists()


def test_real_http_error_alone_marks_partial(image,tmp_path):
    fetch,sink=fixtures(tmp_path)
    def missing(url,**kwargs):
        response=fetch(url,**kwargs)
        if url.endswith('data.json'):
            response.update(status=404,body=b'{"level":47}')
        return response
    result=render_isolated(ROOT,image_id=image,fetch_response=missing,original_sink=sink,enabled=True)
    assert result.text=='Level 47'  # Browser still sees the real response body.
    assert result.status=='partial'
    assert result.blocked_requests==[{'url':ROOT+'data.json','reason':'http_error','status':404,'resource_type':'fetch','response_delivered':True}]
    assert any(a['status']==404 for a in result.assets)
