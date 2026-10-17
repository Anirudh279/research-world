"""Single bounded frozen tool request. No Store, providers or source networking."""
import json
from pathlib import Path
import sys
from .core import Objects,Replay,SimError,encoded

MAX_REQUEST=16384
MAX_RESPONSE=256*1024


def execute(snapshot_id, request):
    replay=Replay(Objects('/archive'),snapshot_id)
    if not isinstance(request,dict):raise SimError('invalid_request','Expected a tool request.')
    tool=request.get('tool');args=request.get('args',{})
    if not isinstance(args,dict):raise SimError('invalid_request','Expected tool arguments.')
    if tool=='ready':result={'ready':True,'snapshot_id':snapshot_id}
    elif tool=='search':result=replay.search(**args)
    elif tool=='open':result=replay.read(**args)
    elif tool=='find':result=replay.find(**args)
    elif tool=='follow':
        doc=replay.follow(**args);result=replay.read(doc['id'])
    else:raise SimError('unknown_action','Choose search, open, find or follow.')
    return {'snapshot_id':snapshot_id,'result':result}


def main():
    try:
        raw=sys.stdin.buffer.read(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST:raise SimError('request_limit','Tool request exceeds its limit.')
        response=execute(sys.argv[1],json.loads(raw))
        output=encoded(response)
        if len(output)>MAX_RESPONSE:raise SimError('response_limit','Tool response exceeds its limit.')
    except SimError as exc:output=encoded({'error':{'code':exc.code,'message':exc.message}})
    except Exception:output=encoded({'error':{'code':'worker_failed','message':'Frozen tool execution failed.'}})
    sys.stdout.buffer.write(output)


if __name__=='__main__':main()
