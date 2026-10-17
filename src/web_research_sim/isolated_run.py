"""Durable local admission for a hosted-model run using isolated replay tools."""
from .core import SimError, digest, encoded


def run_once(store, snapshot_id, *, request_key, provider, model, image_id, max_steps=8, runner=None):
    if not isinstance(request_key,str) or not 1 <= len(request_key) <= 200:
        raise SimError('invalid_request_key','Use a stable request key of 1–200 characters.')
    if provider not in ('openai','anthropic'):
        raise SimError('invalid_provider','Choose openai or anthropic for the hosted model broker.')
    request = {'snapshot_id':snapshot_id,'provider':provider,'model':model,'image_id':image_id,'max_steps':max_steps}
    fingerprint = digest(encoded(request))
    with store.db(write=True) as db:
        db.execute('CREATE TABLE IF NOT EXISTS isolated_run_requests(request_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT, error_code TEXT)')
        prior = db.execute('SELECT * FROM isolated_run_requests WHERE request_key=?',(request_key,)).fetchone()
        if prior:
            if prior['request_hash'] != fingerprint:
                raise SimError('idempotency_conflict','Request key is already bound to different inputs.',409)
            return {'request_key':request_key,'status':prior['status'],'run_id':prior['run_id'],'error_code':prior['error_code'],'resubmitted':False}
        db.execute("INSERT INTO isolated_run_requests VALUES(?,?,'uncertain',NULL,NULL)",(request_key,fingerprint))
    try:
        if runner is None:
            from .isolated_replay import IsolatedReplay
            from .workflow import Workflow
            runner = Workflow(store,replay_factory=lambda objects,snapshot:IsolatedReplay(objects,snapshot,image_id=image_id)).research
        run = runner(snapshot_id,'isolated:'+digest(request_key.encode()),model_provider=provider,model=model,max_steps=max_steps)
    except Exception as exc:
        code = exc.code if isinstance(exc,SimError) else 'isolated_run_failed'
        with store.db(write=True) as db:
            db.execute('UPDATE isolated_run_requests SET error_code=? WHERE request_key=?',(code,request_key))
        raise SimError(code,'Run failed or is uncertain. This request key will not resubmit it; inspect saved state before another attempt.') from None
    with store.db(write=True) as db:
        db.execute("UPDATE isolated_run_requests SET status='completed',run_id=? WHERE request_key=?",(run['id'],request_key))
    return {'request_key':request_key,'status':'completed','run_id':run['id'],'error_code':None,'resubmitted':False}
