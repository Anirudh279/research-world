"""Opt-in controlled-fixture browser prototype, not a certified live renderer.

No live fetch path is enabled until OS egress containment is independently proved.
Every browser HTTP request is fulfilled from caller-owned synthetic fixture bytes
through the controller, after its original_sink succeeds. No native fetch fallback.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from .capture import CaptureError, _parse_url


@dataclass
class RenderedCapture:
    url: str
    source_sha256: str
    rendered_html: bytes
    text: str
    title: str
    assets: list[dict]
    blocked_requests: list[dict]
    rendered_at: str
    browser_version: str
    derived_receipt: object
    isolation: str = 'controlled_fixture_only; OS_network_isolation_unverified'
    status: str = 'completed'


_SCRIPT = r'''
const readline = require('readline');
const cfg = JSON.parse(process.argv[2]);
const {chromium} = require(cfg.playwright);
const rl = readline.createInterface({input:process.stdin});
const pending = new Map(); let counter=0;
function send(v){ process.stdout.write(JSON.stringify(v)+'\n'); }
rl.on('line', line => { const r=JSON.parse(line); const f=pending.get(r.id); if(f){pending.delete(r.id);f(r);} });
function request(v){ return new Promise(resolve=>{ const id=++counter; pending.set(id,resolve);send({...v,id}); }); }
(async()=>{
 const browser=await chromium.launch({executablePath:cfg.browser,headless:true,
   proxy:{server:'http://127.0.0.1:9',bypass:'<-loopback>'},
   args:['--disable-background-networking','--disable-component-update','--disable-sync',
     '--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
     '--js-flags=--max-old-space-size=128','--research-render-token='+cfg.token]});
 try {
 const context=await browser.newContext({serviceWorkers:'block',acceptDownloads:false});
 if(typeof context.routeWebSocket!=='function') throw new Error('websocket_interception_unavailable');
 await context.routeWebSocket('**/*', ws=>{send({kind:'blocked',url:ws.url(),reason:'websocket_disabled'});ws.close();});
 await context.route('**/*',async route=>{
   const req=route.request();
   if(req.method()!=='GET'){send({kind:'blocked',url:req.url(),reason:'non_get_disabled'});await route.abort();return;}
   const response=await request({kind:'request',url:req.url(),resource_type:req.resourceType()});
   if(!response.ok){await route.abort();return;}
   await route.fulfill({status:response.status,headers:response.headers,body:Buffer.from(response.body,'base64')});
 });
 const page=await context.newPage();
 context.on('page',p=>{if(p!==page){send({kind:'blocked',url:p.url(),reason:'popup_disabled'});p.close().catch(()=>{});}});
 page.on('dialog',d=>d.dismiss());
 page.on('download',d=>d.cancel());
 await page.goto(cfg.url,{waitUntil:'load',timeout:cfg.timeout_ms});
 await page.waitForTimeout(cfg.settle_ms);
 const state=await page.evaluate(cap=>{
   const html=document.documentElement.outerHTML;
   const text=document.body?.innerText||'';
   if(html.length>cap||text.length>cap)throw new Error('rendered_output_limit');
   return {html,text,title:document.title,url:location.href};
 },cfg.output_limit);
 send({kind:'result',...state,browser_version:browser.version()});
 } finally {await browser.close();rl.close();}
})().catch(e=>{send({kind:'error',code:String(e.message).slice(0,200)});process.exitCode=1;rl.close();});
'''


def _fixture_url(url):
    parsed,host,_ = _parse_url(url)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise CaptureError('fixture_ip_destination_forbidden')
    if not (host.endswith(('.test','.example')) or host in ('example.com','example.net','example.org') or host.endswith(('.example.com','.example.org','.example.net'))):
        raise CaptureError('fixture_hostname_required')
    return parsed


def render_fixture(url,resources,*,original_sink,node_executable,playwright_module,browser_executable,
                   enabled=False,max_requests=30,max_resource_bytes=2*1024**2,max_total_bytes=10*1024**2,
                   max_seconds=20,settle_ms=250):
    """Run actual JS against synthetic {url:{body:bytes,status,headers}} mappings.

    Dependencies are explicit paths; nothing is downloaded or installed. Originals
    are persisted by the mandatory sink before a response reaches the browser.
    This prototype intentionally rejects live websites and private literal IPs.
    """
    if enabled is not True: raise CaptureError('rendering_requires_explicit_opt_in')
    if not callable(original_sink): raise CaptureError('original_sink_required')
    _fixture_url(url)
    if not isinstance(resources,dict) or url not in resources: raise CaptureError('root_fixture_missing')
    if any(type(v) is not int for v in (max_requests,max_resource_bytes,max_total_bytes,settle_ms)) or not 1<=max_requests<=100 or not 1<=max_resource_bytes<=2*1024**2 or not 1<=max_total_bytes<=10*1024**2 or not 0<=settle_ms<=2000 or not 1<=max_seconds<=60:
        raise CaptureError('invalid_render_limits')
    # Snapshot caller mappings before browser execution; retain only supported
    # harmless response headers. Redirects are denied, never followed implicitly.
    fixtures={}
    for key,item in resources.items():
        _fixture_url(key)
        if not isinstance(item,dict) or not isinstance(item.get('body'),bytes): raise CaptureError('invalid_fixture')
        status=item.get('status',200)
        if type(status) is not int or not 200<=status<=599: raise CaptureError('invalid_fixture_status')
        headers={str(k).lower():str(v) for k,v in item.get('headers',{}).items() if str(k).lower() in ('content-type','location')}
        fixtures[key]={'body':item['body'],'status':status,'headers':headers}
    output_limit=2*1024**2
    assets=[]; blocked=[]; total=0; count=0; source_hash=None; result=None
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='research-render-') as temp:
        script=Path(temp)/'render.cjs';script.write_text(_SCRIPT)
        cfg={'url':url,'playwright':str(playwright_module),'browser':str(browser_executable),
             'timeout_ms':int(max_seconds*1000),'settle_ms':settle_ms,'output_limit':output_limit,'token':uuid.uuid4().hex}
        # Do not pass API credentials or the user's browser profile to the child.
        env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','TMPDIR','LANG')}
        env['HOME']=temp
        env['TMPDIR']=temp
        proc=subprocess.Popen([str(node_executable),str(script),json.dumps(cfg)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,start_new_session=True,bufsize=0)
        selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
        buffer=b''
        try:
            while result is None:
                remaining=max_seconds-(time.monotonic()-started)
                if remaining<=0: raise CaptureError('render_wallclock_limit')
                events=selector.select(min(remaining,0.2))
                if not events:
                    if proc.poll() is not None: raise CaptureError('browser_terminated_without_result')
                    continue
                chunk=os.read(proc.stdout.fileno(),65536)
                if not chunk: raise CaptureError('browser_terminated_without_result')
                buffer+=chunk
                if len(buffer)>8*1024**2: raise CaptureError('browser_output_limit')
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    message=json.loads(line)
                    if message['kind']=='error': raise CaptureError('browser_render_failed:'+message['code'])
                    if message['kind']=='blocked':
                        blocked.append(message)
                        if len(blocked)>max_requests: raise CaptureError('blocked_request_limit')
                        continue
                    if message['kind']=='result': result=message;break
                    if message['kind']!='request': raise CaptureError('browser_protocol_error')
                    count+=1; target=message['url']; response={'id':message['id'],'ok':False}
                    if count>max_requests: raise CaptureError('request_limit')
                    reason=None
                    try:
                        _fixture_url(target)
                        item=fixtures.get(target)
                        if item is None: raise CaptureError('fixture_resource_missing')
                        body=item['body']
                        if len(body)>max_resource_bytes or total+len(body)>max_total_bytes: raise CaptureError('resource_byte_limit')
                        total+=len(body)
                        sha=hashlib.sha256(body).hexdigest()
                        metadata={'url':target,'requested_url':target,'status':item['status'],
                                  'captured_at':datetime.now(timezone.utc).isoformat(),'content_type':item['headers'].get('content-type',''),
                                  'kind':'synthetic_original_before_render','sha256':sha,'bytes':len(body)}
                        receipt=original_sink(body,metadata)
                        assets.append({**metadata,'receipt':receipt,'resource_type':message['resource_type']})
                        if target==url and source_hash is None: source_hash=sha
                        if 300<=item['status']<400: raise CaptureError('fixture_redirect_disabled')
                        response.update(ok=True,body=base64.b64encode(body).decode(),headers=item['headers'],status=item['status'])
                    except CaptureError as exc:
                        reason=str(exc);blocked.append({'url':target,'reason':reason,'kind':'blocked'})
                    if time.monotonic()-started>=max_seconds: raise CaptureError('render_wallclock_limit')
                    proc.stdin.write((json.dumps(response)+'\n').encode());proc.stdin.flush()
            html=result['html'].encode()
            if len(html)>output_limit or len(result['text'].encode())>output_limit: raise CaptureError('rendered_output_limit')
            rendered_at=datetime.now(timezone.utc).isoformat()
            receipt=original_sink(html,{'url':result['url'],'kind':'derived_rendered_dom','captured_at':rendered_at,
                                       'source_sha256':source_hash,'sha256':hashlib.sha256(html).hexdigest()})
            return RenderedCapture(result['url'],source_hash,html,result['text'],result['title'],assets,blocked,
                rendered_at,result['browser_version'],receipt,status='partial' if blocked else 'completed')
        finally:
            selector.close()
            if proc.poll() is None:
                try: os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try: os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
            proc.wait(timeout=1)
            # Playwright launches Chromium in its own process group. A killed
            # controller does not kill that group. Match a unique per-run token,
            # never a broad browser name or another user's Chrome profile.
            rows=subprocess.check_output(['ps','-axo','pid,pgid,command'],text=True,timeout=2).splitlines()
            for row in rows:
                fields=row.strip().split(None,2)
                if len(fields)!=3: continue
                pid,group,command=fields
                if ('--research-render-token='+cfg['token']) in command and command.startswith(str(browser_executable)+' ') and pid==group:
                    try: os.killpg(int(group),signal.SIGKILL)
                    except ProcessLookupError: pass
            proc.stdin.close();proc.stdout.close()


def render_live(*args,**kwargs):
    raise CaptureError('live_rendering_disabled_until_OS_network_boundary_verified')
