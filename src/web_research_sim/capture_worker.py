"""Fresh-process static fetch/extract worker. Input/output are bounded JSON."""
import base64
from dataclasses import asdict
import json
from pathlib import Path
import sys
from .capture import fetch_page,capture_diagnostic,CaptureError
from .core import Store,identifier

MAX_INPUT=16*1024
MAX_OUTPUT=12*1024**2


def execute(request):
    if not isinstance(request,dict) or set(request)!={'root','world_id','url','max_bytes'}:
        raise CaptureError('capture_failed')
    if not isinstance(request['root'],str) or not 1<=len(request['root'])<=4096 or not Path(request['root']).is_absolute():
        raise CaptureError('capture_failed')
    identifier(request['world_id'])
    if type(request['max_bytes']) is not int or not 1<=request['max_bytes']<=2*1024**2:
        raise CaptureError('response_too_large')
    store=Store(request['root']);store.world(request['world_id'])
    page=fetch_page(request['url'],max_bytes=request['max_bytes'],
        original_sink=lambda body,meta:store.preserve_original(request['world_id'],body,meta))
    if len(page.text.encode())>4*1024**2 or len(json.dumps(page.headers).encode())>64*1024:
        raise CaptureError('response_too_large')
    result=asdict(page);result['body_base64']=base64.b64encode(result.pop('body')).decode()
    return {'ok':True,'page':result}


def main():
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT:raise CaptureError('capture_failed')
        request=json.loads(raw)
        response=execute(request)
        output=json.dumps(response,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if len(output)>MAX_OUTPUT:raise CaptureError('response_too_large')
    except BaseException as exc:
        # Never print exception text, stack traces, source bodies or credentials.
        output=json.dumps({'ok':False,'diagnostic':capture_diagnostic(exc)},separators=(',',':')).encode()
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush()


if __name__=='__main__':main()
