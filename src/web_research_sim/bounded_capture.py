"""A wall-clock bound around public static capture, persistence and extraction."""
import base64
from dataclasses import fields
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from .capture import CapturedPage,CaptureError,_parse_url
from .core import identifier,Store
from .capture_worker import MAX_INPUT,MAX_OUTPUT


def _run_worker(command,request,*,timeout_seconds,max_output_bytes=MAX_OUTPUT,env=None):
    """Internal runner; production command is fixed by fetch_bounded.

    A separate session owns all worker/PDF descendants. No shell interpolation.
    Nonblocking pipes enforce the same deadline for writes and reads.
    """
    if len(request)>MAX_INPUT:raise CaptureError('capture_failed')
    started=time.monotonic();deadline=started+timeout_seconds
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                             start_new_session=True,bufsize=0,env=env)
    selector=selectors.DefaultSelector()
    os.set_blocking(process.stdin.fileno(),False);os.set_blocking(process.stdout.fileno(),False)
    selector.register(process.stdin,selectors.EVENT_WRITE,'write')
    selector.register(process.stdout,selectors.EVENT_READ,'read')
    output=bytearray();offset=0
    try:
        eof=False
        while not eof:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise CaptureError('capture_timeout')
            events=selector.select(remaining)
            if not events:raise CaptureError('capture_timeout')
            for key,_ in events:
                if key.data=='write':
                    try:offset+=os.write(key.fd,request[offset:offset+4096])
                    except BlockingIOError:continue
                    except BrokenPipeError:raise CaptureError('capture_failed') from None
                    if offset==len(request):
                        selector.unregister(process.stdin);process.stdin.close()
                else:
                    try:chunk=os.read(key.fd,min(65536,max_output_bytes+1-len(output)))
                    except BlockingIOError:continue
                    if not chunk:eof=True;break
                    output.extend(chunk)
                    if len(output)>max_output_bytes:raise CaptureError('response_too_large')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise CaptureError('capture_timeout')
        try:status=process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:raise CaptureError('capture_timeout') from None
        if status!=0:raise CaptureError('capture_failed')
        return bytes(output)
    finally:
        selector.close()
        # Includes a PDF extractor inheriting the worker's process group. This
        # also removes children that outlive an otherwise completed worker.
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        except PermissionError:
            if process.poll() is None:process.kill()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:raise CaptureError('capture_cleanup_failed') from None
        if not process.stdin.closed:process.stdin.close()
        process.stdout.close()


def fetch_bounded(store_or_root,world_id,url,*,max_bytes=2*1024**2,timeout_seconds=30):
    """Return CapturedPage; reserve/settle calls remain the Workflow's responsibility.

    The subprocess opens a fresh Store and commits originals before extracting.
    A timeout is uncertain: original commit may already exist, never auto-retry.
    """
    root=store_or_root.root if isinstance(store_or_root,Store) else Path(store_or_root)
    identifier(world_id);_parse_url(url)
    if type(max_bytes) is not int or not 1<=max_bytes<=2*1024**2 or type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=120:
        raise ValueError('Bounded capture requires 1..2 MiB and a positive deadline at most 120 seconds')
    request=json.dumps({'root':str(Path(root).resolve()),'world_id':world_id,'url':url,'max_bytes':max_bytes},separators=(',',':'),allow_nan=False).encode()
    # Explicit package parent enables checkout execution without forwarding an
    # arbitrary PYTHONPATH, user site modules, API credentials or proxy variables.
    env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','LANG','TMPDIR')}
    from .storage import storage_config, CREDENTIAL_ENV
    if storage_config(Path(root))['backend']=='s3':
        env.update({k:os.environ[k] for k in CREDENTIAL_ENV if k in os.environ})
    env.update(PYTHONPATH=str(Path(__file__).resolve().parent.parent),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    raw=_run_worker([sys.executable,'-m','web_research_sim.capture_worker'],request,timeout_seconds=timeout_seconds,env=env)
    try:result=json.loads(raw)
    except (ValueError,UnicodeError):raise CaptureError('capture_failed') from None
    if not isinstance(result,dict):raise CaptureError('capture_failed')
    if result.get('ok') is not True:
        diagnostic=result.get('diagnostic')
        code=diagnostic.get('error_code') if isinstance(diagnostic,dict) else None
        allowed={'private_destination','invalid_url','nonstandard_port','dns_empty','dns_failure','capture_timeout',
                 'tls_failure','response_too_large','incomplete_body','redirect_without_location','too_many_redirects',
                 'capture_transport_or_protocol_error','capture_failed'}
        raise CaptureError(code if code in allowed else 'capture_failed')
    page=result.get('page')
    if not isinstance(page,dict):raise CaptureError('capture_failed')
    try:
        body=base64.b64decode(page.pop('body_base64'),validate=True)
        if len(body)>max_bytes:raise CaptureError('response_too_large')
        page['body']=body
        if set(page)!={field.name for field in fields(CapturedPage)} or page.get('requested_url')!=url:
            raise CaptureError('capture_failed')
        _parse_url(page['url']);identifier(page['original_response_id'])
        if not isinstance(page['text'],str) or not isinstance(page['headers'],dict) or not isinstance(page['links'],list):raise CaptureError('capture_failed')
        return CapturedPage(**page)
    except (ValueError,TypeError,KeyError):raise CaptureError('capture_failed') from None
