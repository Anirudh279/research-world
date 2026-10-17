"""Offline Docker browser; host fetch/persistence runs in deadline-bound children.

Only an exact image digest is accepted. No page bytes enter a browser before its
running container's actual boundary is inspected. Callbacks are trusted host code,
not code supplied by a webpage. Public raw transport must validate/pin every URL.
"""
from dataclasses import asdict,is_dataclass,dataclass
from datetime import datetime,timezone
import base64
import hashlib
import ipaddress
import json
import multiprocessing
import os
import re
import selectors
import signal
import subprocess
import time
import uuid
from urllib.parse import urldefrag,urljoin
from .capture import CaptureError,_parse_url
from .rendered_capture import _SCRIPT


@dataclass
class IsolatedRenderedCapture:
    url: str
    requested_url: str
    source_sha256: str
    rendered_html: bytes
    text: str
    title: str
    assets: list[dict]
    blocked_requests: list[dict]
    rendered_at: str
    browser_version: str
    derived_receipt: object
    boundary: dict
    status: str
    isolation: str = 'browser_container_network_none; host_fetch_callbacks_outside_container'


def _run(args,timeout=10):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,check=False)
    if result.returncode: raise CaptureError('docker_operation_failed')
    return result.stdout


def validate_boundary(data,image_id):
    """Check actual inspection data, never merely the requested Docker flags."""
    config=data.get('Config',{}); host=data.get('HostConfig',{}); state=data.get('State',{})
    options=host.get('SecurityOpt') or []
    tmpfs=host.get('Tmpfs') or {}
    if (not state.get('Running') or data.get('Image')!=image_id or config.get('Image')!=image_id
        or config.get('User')!='pwuser' or host.get('NetworkMode')!='none'
        or host.get('ReadonlyRootfs') is not True or host.get('Privileged')
        or host.get('NanoCpus')!=2_000_000_000 or host.get('Memory')!=1024**3
        or host.get('MemorySwap')!=1024**3 or host.get('CapAdd')
        or host.get('PidsLimit')!=128 or set(host.get('CapDrop') or [])!={'ALL'}
        or not any(o in ('no-new-privileges','no-new-privileges=true') for o in options)
        or data.get('Mounts') or host.get('Binds') or host.get('Devices')
        or set(tmpfs)!={'/tmp'} or 'size=268435456' not in tmpfs['/tmp']
        or 'nosuid' not in tmpfs['/tmp'] or 'nodev' not in tmpfs['/tmp']
        or host.get('PidMode') or host.get('IpcMode')=='host'
        or host.get('PortBindings')):
        raise CaptureError('renderer_boundary_mismatch')
    return {'container_id':data['Id'],'image_id':data['Image'],'running':True,'network':'none',
            'readonly_root':True,'user':'pwuser','cpus':2,'memory_bytes':1024**3,'pids':128,
            'tmpfs_bytes':268435456,'host_mounts':0,'capabilities_dropped':['ALL'],
            'no_new_privileges':True,'checked_at':datetime.now(timezone.utc).isoformat()}


def _child(pipe,operation,callback,sink,url,cap,remaining,payload):
    try:
        os.setsid()
        if operation=='fetch':
            _,host,_=_parse_url(url)
            try: ip=ipaddress.ip_address(host)
            except ValueError: ip=None
            if ip is not None and (not ip.is_global or ip.is_multicast):
                raise CaptureError('private_destination')
            response=callback(url,max_bytes=cap,timeout_seconds=min(20,remaining))
            response=asdict(response) if is_dataclass(response) else response
            if not isinstance(response,dict) or not isinstance(response.get('body'),bytes): raise CaptureError('invalid_raw_response')
            body=response['body']
            if len(body)>cap: raise CaptureError('resource_byte_limit')
            if response.get('url')!=url: raise CaptureError('raw_transport_must_return_single_hop')
            headers={str(k).lower():str(v) for k,v in response.get('headers',{}).items()}
            status=response.get('status')
            if type(status) is not int or not 200<=status<=599: raise CaptureError('invalid_response_status')
            sha=hashlib.sha256(body).hexdigest()
            metadata={'url':url,'requested_url':url,'status':status,'captured_at':response.get('captured_at'),
                      'content_type':headers.get('content-type',''),'sha256':sha,'bytes':len(body),'kind':'original_before_render'}
            receipt=sink(body,metadata)  # Must become durable before browser fulfillment.
            if len(json.dumps(receipt).encode())>16384: raise CaptureError('receipt_limit')
            if headers.get('content-encoding','identity').lower()!='identity':
                pipe.send({'ok':False,'error_type':'CaptureError','error':'encoded_response_unsupported','asset':{**metadata,'receipt':receipt}})
                return
            safe={'content-type','location','content-security-policy','x-content-type-options'}
            pipe.send({'ok':True,'body':body,'status':status,'headers':{k:v for k,v in headers.items() if k in safe},'asset':{**metadata,'receipt':receipt}})
        else:
            receipt=sink(payload['body'],payload['metadata'])
            if len(json.dumps(receipt).encode())>16384: raise CaptureError('receipt_limit')
            pipe.send({'ok':True,'receipt':receipt})
    except BaseException as exc:
        pipe.send({'ok':False,'error_type':type(exc).__name__,'error':str(exc)[:100] if isinstance(exc,CaptureError) else 'callback_failed'})
    finally: pipe.close()


def _bounded(operation,callback,sink,url,cap,deadline,payload=None):
    remaining=deadline-time.monotonic()
    if remaining<=0: raise CaptureError('render_wallclock_limit')
    # Unix fork supports injected trusted functions without serializing code.
    # Every invocation owns its own process and is joined/killed before return.
    ctx=multiprocessing.get_context('fork')
    parent,child=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_child,args=(child,operation,callback,sink,url,cap,remaining,payload),daemon=True)
    process.start();child.close()
    try:
        if not parent.poll(max(0,deadline-time.monotonic())): raise CaptureError('render_callback_deadline')
        result=parent.recv()
        if time.monotonic()>deadline: raise CaptureError('render_callback_deadline')
        return result
    except EOFError: raise CaptureError('render_callback_terminated') from None
    finally:
        parent.close()
        process.join(timeout=0.05)
        if process.is_alive():
            try: os.killpg(process.pid,signal.SIGKILL)
            except (ProcessLookupError,PermissionError):
                if process.is_alive(): process.kill()
        process.join(timeout=1)
        if process.is_alive(): raise CaptureError('render_callback_cleanup_failed')


def _write_deadline(stream,raw,deadline):
    fd=stream.fileno();os.set_blocking(fd,False)
    poll=selectors.DefaultSelector();poll.register(fd,selectors.EVENT_WRITE)
    offset=0
    try:
        while offset<len(raw):
            remaining=deadline-time.monotonic()
            if remaining<=0 or not poll.select(remaining): raise CaptureError('render_ipc_deadline')
            try: offset+=os.write(fd,raw[offset:offset+65536])
            except BlockingIOError: continue
    finally: poll.close()


def render_isolated(url,*,image_id,fetch_response,original_sink,enabled=False,max_requests=30,
                    max_resource_bytes=2*1024**2,max_total_bytes=10*1024**2,max_seconds=45,settle_ms=250):
    if enabled is not True: raise CaptureError('rendering_requires_explicit_opt_in')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id): raise CaptureError('immutable_renderer_image_required')
    if not callable(fetch_response) or not callable(original_sink): raise CaptureError('trusted_fetch_and_sink_required')
    _parse_url(url)
    if any(type(v) is not int for v in (max_requests,max_resource_bytes,max_total_bytes,settle_ms,max_seconds)) or not 1<=max_requests<=100 or not 1<=max_resource_bytes<=2*1024**2 or not 1<=max_total_bytes<=10*1024**2 or not 5<=max_seconds<=120 or not 0<=settle_ms<=2000:
        raise CaptureError('invalid_render_limits')
    started=time.monotonic();deadline=started+max_seconds
    name='research-render-'+uuid.uuid4().hex
    cfg={'url':url,'playwright':'/opt/render/node_modules/playwright','browser':'',
         'timeout_ms':max_seconds*1000,'settle_ms':settle_ms,'output_limit':2*1024**2,'token':uuid.uuid4().hex}
    script=_SCRIPT.replace("const {chromium} = require(cfg.playwright);","const {chromium} = require(cfg.playwright); cfg.browser=chromium.executablePath();").replace(".slice(0,200)",".slice(0,4000)")
    # Playwright routes only the first URL in an automatic redirect chain.
    # Complete an empty intermediate document and explicitly navigate each main-document hop so every new URL is
    # intercepted again. Subresource redirects are refused, never bypassed.
    script=script.replace("await context.route('**/*',async route=>{", "let mainRedirect=null; let page; await context.route('**/*',async route=>{")
    script=script.replace("const page=await context.newPage();", "page=await context.newPage();")
    script=script.replace("await route.fulfill({status:response.status", "if(response.redirect){ if(req.isNavigationRequest() && req.frame()===page.mainFrame()){ mainRedirect=response.redirect; await route.fulfill({status:200,contentType:'text/html',body:''}); }else { send({kind:'blocked',url:req.url(),reason:'subframe_redirect_disabled'}); await route.abort(); } return; } await route.fulfill({status:response.status")
    script=script.replace("await page.goto(cfg.url,{waitUntil:'load',timeout:cfg.timeout_ms});", "let navigation=cfg.url; for(let hop=0;hop<6;hop++){ mainRedirect=null; await page.goto(navigation,{waitUntil:'load',timeout:cfg.timeout_ms}); if(!mainRedirect)break; navigation=mainRedirect; if(hop===5)throw new Error('redirect_limit'); }")
    proc=None;selector=None;created=False;assets=[];blocked=[];total=0;count=0;result=None;source_hash=None
    try:
        created=True  # Creation uncertainty still requires cleanup by our unique name.
        _run(['docker','create','--name',name,'--network','none','--cpus','2','--memory','1g','--memory-swap','1g',
              '--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only',
              '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456','--user','pwuser','--ipc','private',
              '--env','HOME=/tmp','--env','XDG_CONFIG_HOME=/tmp','--env','XDG_CACHE_HOME=/tmp',
              '-i','--entrypoint','node',image_id,'-e',script,'unused',json.dumps(cfg)],timeout=min(10,max_seconds))
        created=True
        proc=subprocess.Popen(['docker','start','-ai',name],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise CaptureError('render_wallclock_limit')
            inspected=json.loads(_run(['docker','inspect',name],timeout=min(5,remaining)))[0]
            if inspected['State']['Running']: break
            if proc.poll() is not None: raise CaptureError('renderer_start_failed')
            time.sleep(0.05)
        boundary=validate_boundary(inspected,image_id)
        selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ);buffer=b''
        while result is None:
            remaining=deadline-time.monotonic()
            if remaining<=0: raise CaptureError('render_wallclock_limit')
            if not selector.select(min(remaining,.2)):
                if proc.poll() is not None: raise CaptureError('renderer_terminated_without_result')
                continue
            chunk=os.read(proc.stdout.fileno(),65536)
            if not chunk: raise CaptureError('renderer_terminated_without_result')
            buffer+=chunk
            if len(buffer)>8*1024**2: raise CaptureError('browser_output_limit')
            while b'\n' in buffer:
                line,buffer=buffer.split(b'\n',1);message=json.loads(line)
                kind=message.get('kind')
                if kind=='error': raise CaptureError('browser_render_failed:'+message['code'])
                if kind=='blocked':
                    blocked.append(message)
                    if len(blocked)>max_requests: raise CaptureError('blocked_request_limit')
                    continue
                if kind=='result': result=message;break
                if kind!='request': raise CaptureError('browser_protocol_error')
                count+=1
                if count>max_requests: raise CaptureError('request_limit')
                cap=min(max_resource_bytes,max_total_bytes-total)
                if cap<=0: raise CaptureError('total_resource_byte_limit')
                reply=_bounded('fetch',fetch_response,original_sink,message['url'],cap,deadline)
                wire={'id':message['id'],'ok':reply['ok']}
                if reply['ok']:
                    total+=len(reply['body']); asset={**reply['asset'],'resource_type':message['resource_type']};assets.append(asset)
                    if source_hash is None and message['resource_type']=='document': source_hash=asset['sha256']
                    wire.update(body=base64.b64encode(reply['body']).decode(),status=reply['status'],headers=reply['headers'])
                    if reply['status'] >= 400:
                        # Preserve and deliver the actual error response so page
                        # code observes its status, but never call it complete.
                        blocked.append({'url': message['url'], 'reason': 'http_error',
                                        'status': reply['status'], 'resource_type': message['resource_type'],
                                        'response_delivered': True})
                    if 300<=reply['status']<400:
                        try:
                            if not reply['headers'].get('location'): raise CaptureError('redirect_without_location')
                            target=urljoin(message['url'],reply['headers']['location'])
                            _,host,_=_parse_url(target)
                            try: ip=ipaddress.ip_address(host)
                            except ValueError: ip=None
                            if ip is not None and (not ip.is_global or ip.is_multicast): raise CaptureError('private_destination')
                            if message['resource_type']!='document': raise CaptureError('subresource_redirect_disabled')
                            wire={'id':message['id'],'ok':True,'redirect':target}
                        except CaptureError as exc:
                            wire={'id':message['id'],'ok':False}
                            blocked.append({'url':message['url'],'reason':str(exc)})

                else:
                    if reply.get('asset'):
                        assets.append({**reply['asset'],'resource_type':message['resource_type']});total+=reply['asset']['bytes']
                    else: total+=cap  # Unknown callback read outcome consumes its reserved byte ceiling.
                    blocked.append({'url':message['url'],'reason':reply['error'],'error_type':reply['error_type']})
                # IPC is local to an offline worker. Small writes are drained by
                # its readline handler; the overall deadline is checked per message.
                _write_deadline(proc.stdin,(json.dumps(wire)+'\n').encode(),deadline)
        final_url=urldefrag(result['url'])[0]
        final_documents=[a for a in assets if a['url']==final_url and a['resource_type']=='document' and not 300<=a['status']<400]
        if not final_documents: raise CaptureError('final_document_original_missing')
        source_hash=final_documents[-1]['sha256']
        html=result['html'].encode();text=result['text']
        if len(html)>2*1024**2 or len(text.encode())>2*1024**2: raise CaptureError('rendered_output_limit')
        at=datetime.now(timezone.utc).isoformat()
        saved=_bounded('save',None,original_sink,None,0,deadline,{'body':html,'metadata':{'url':result['url'],'kind':'derived_rendered_dom','captured_at':at,'source_sha256':source_hash,'sha256':hashlib.sha256(html).hexdigest(),'bytes':len(html)}})
        if not saved['ok']: raise CaptureError('derived_persistence_failed')
        return IsolatedRenderedCapture(result['url'],url,source_hash,html,text,result['title'],assets,blocked,at,result['browser_version'],saved['receipt'],boundary,'partial' if blocked else 'completed')
    finally:
        if selector: selector.close()
        cleanup_error=None
        if created:
            try:
                removal=subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
                if removal.returncode and b'No such container' not in removal.stderr:
                    raise CaptureError('docker_removal_failed')
                remaining=_run(['docker','ps','-aq','--filter','name=^/'+name+'$'],timeout=3).strip()
                if remaining: raise CaptureError('container_still_present')
            except (CaptureError,subprocess.TimeoutExpired):
                cleanup_error=CaptureError('renderer_cleanup_uncertain:'+name)
                cleanup_error.container_name=name
        if proc:
            if proc.poll() is None: proc.kill()
            proc.wait(timeout=2)
            proc.stdin.close();proc.stdout.close()
        if cleanup_error: raise cleanup_error
