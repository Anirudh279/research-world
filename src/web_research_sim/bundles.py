"""Portable snapshots; ZIP member names never become filesystem destinations."""
import io
import json
import zipfile
from .core import SimError, Replay, encoded, digest, identifier, timestamp, artifact_references

MAX_ENTRY = 16 * 1024**2
MAX_TOTAL = 256 * 1024**2
MAX_ENTRIES = 10001
SCHEMA = 'research-challenge-v1'


def bad(message):
    return SimError('invalid_bundle', message)


def export_snapshot(store, snapshot_id, *, redistribution_acknowledged=False, metadata_only=False):
    replay = store.replay(snapshot_id)
    m = replay.manifest
    if metadata_only:
        descriptor = {'schema':SCHEMA,'mode':'metadata_only','reproducible':False,'snapshot_id':snapshot_id,
            'question':m['question'],'cutoff':m['cutoff'],
            'sources':[{'url':r['url'],'captured_at':r['captured_at'],'body_sha256':r['body_sha256']} for r in m['records']],
            'notice':'Content omitted. Recapture creates a different world.'}
        files = {'challenge.json':encoded(descriptor)}
    else:
        if redistribution_acknowledged is not True:
            raise SimError('redistribution_required','Confirm permission to redistribute every included source.')
        hashes = {snapshot_id}
        for r in m['records']:
            replay.open(r['id'])
            hashes.update((r['body_sha256'],r['text_sha256']))
            hashes.update(a['sha256'] for a in artifact_references(r))
        files = {'objects/'+sha:store.objects.get(sha) for sha in hashes}
        files['challenge.json'] = encoded({'schema':SCHEMA,'mode':'content','reproducible':True,
            'snapshot_id':snapshot_id,'redistribution_acknowledged':True})
    if len(files)>MAX_ENTRIES or sum(map(len,files.values()))>MAX_TOTAL:
        raise bad('Export exceeds limits.')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,body in sorted(files.items()):
            if len(body)>MAX_ENTRY: raise bad('Entry exceeds limit.')
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.external_attr=0o100600<<16
            archive.writestr(info,body)
    return output.getvalue()


class _MemoryObjects:
    def __init__(self,files): self.files=files
    def get(self,sha):
        identifier(sha,64)
        body=self.files.get('objects/'+sha)
        if body is None or digest(body)!=sha: raise bad('Missing or corrupt object.')
        return body


def _json(body):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise bad('Duplicate JSON key.')
            result[key]=value
        return result
    def constant(value): raise bad('Invalid JSON number.')
    try: return json.loads(body,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,UnicodeError,RecursionError): raise bad('Invalid JSON.') from None


def import_snapshot(store,bundle):
    if not isinstance(bundle,bytes) or len(bundle)>MAX_TOTAL+MAX_ENTRIES*300:
        raise bad('Bundle must be bounded ZIP bytes.')
    files={}
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            infos=archive.infolist()
            if len(infos)>MAX_ENTRIES or sum(i.file_size for i in infos)>MAX_TOTAL:
                raise bad('Bundle exceeds entry or total limit.')
            for info in infos:
                name=info.filename
                if name in files or info.is_dir() or info.flag_bits & 1:
                    raise bad('Duplicate, directory or encrypted member.')
                if name!='challenge.json':
                    if not name.startswith('objects/') or len(name)!=72: raise bad('Unexpected entry path.')
                    identifier(name[8:],64)
                if info.file_size>MAX_ENTRY or info.compress_type!=zipfile.ZIP_STORED:
                    raise bad('Oversize or compressed entry unsupported.')
                if (info.external_attr>>16)&0o170000 not in (0,0o100000):
                    raise bad('Nonregular entry unsupported.')
                with archive.open(info) as member: body=member.read(MAX_ENTRY+1)
                if len(body)!=info.file_size or len(body)>MAX_ENTRY: raise bad('Entry size mismatch.')
                files[name]=body
    except (zipfile.BadZipFile,OSError,RuntimeError,EOFError): raise bad('Invalid ZIP container.') from None
    descriptor=_json(files.get('challenge.json',b''))
    if not isinstance(descriptor,dict) or descriptor.get('schema')!=SCHEMA: raise bad('Unknown challenge schema.')
    if descriptor.get('mode')=='metadata_only':
        raise SimError('non_reproducible_bundle','Metadata-only export cannot reproduce a world. Recapture creates a different world.')
    if descriptor.get('mode')!='content' or descriptor.get('reproducible') is not True or descriptor.get('redistribution_acknowledged') is not True:
        raise bad('Missing content declaration.')
    sid=identifier(descriptor.get('snapshot_id'),64)
    memory=_MemoryObjects(files)
    raw=memory.get(sid)
    m=_json(raw)
    if not isinstance(m,dict) or encoded(m)!=raw: raise bad('Manifest must use canonical JSON.')
    try:
        wid=identifier(m['world_id'])
        if not isinstance(m['question'],str) or not 1<=len(m['question'].strip())<=4000: raise bad('Invalid question.')
        timestamp(m['cutoff'])
        if not isinstance(m['records'],list): raise bad('Invalid records.')
        replay=Replay(memory,sid)
        expected={'challenge.json','objects/'+sid}
        for r in m['records']:
            if not isinstance(r.get('url'),str) or not isinstance(r.get('title'),str): raise bad('Invalid metadata.')
            replay.open(r['id'])
            expected.update(('objects/'+r['body_sha256'],'objects/'+r['text_sha256']))
            expected.update('objects/'+a['sha256'] for a in artifact_references(r))
        if set(files)!=expected: raise bad('Missing or unreferenced object.')
    except (KeyError,TypeError,AttributeError,UnicodeError): raise bad('Malformed manifest.') from None
    # Validate everything before any DB mutation. Object publication is atomic;
    # failed DB commit can leave only harmless unreferenced content-addressed bytes.
    with store.db(write=True) as db:
        prior=db.execute('SELECT question FROM worlds WHERE id=?',(wid,)).fetchone()
        if prior and prior['question']!=m['question']:
            raise SimError('world_conflict','Existing world metadata differs.',409)
        existing=db.execute('SELECT world_id,cutoff,count FROM snapshots WHERE id=?',(sid,)).fetchone()
        index=(wid,m['cutoff'],len(m['records']))
        if existing and tuple(existing)!=index: raise SimError('snapshot_conflict','Existing snapshot index differs.',409)
        for name,body in files.items():
            if name.startswith('objects/') and store.objects.put(body)!=name[8:]: raise bad('Object hash mismatch.')
        if not prior:
            # No imported live-spend entitlement. Explicit new allocation required.
            db.execute('INSERT INTO worlds VALUES(?,?,?,?)',(wid,m['question'],m['cutoff'],encoded({k:0 for k in store.KINDS}).decode()))
        db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)',(sid,*index))
    return {'id':sid,'world_id':wid,'count':len(m['records']),'reproducible':True}
