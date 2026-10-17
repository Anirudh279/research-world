"""Durable tool-scoped sessions for external agents; no inference or authentication."""
import json
import uuid
from .core import SimError, encoded, digest, identifier, timestamp


class SessionManager:
    def __init__(self,store):
        self.store=store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS session_creates(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,session_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_sessions(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL,provider TEXT NOT NULL,model TEXT NOT NULL,tool_limit INTEGER NOT NULL,created_at TEXT NOT NULL,parent_id TEXT,submission_hash TEXT,run_id TEXT);
            CREATE TABLE IF NOT EXISTS session_tools(session_id TEXT NOT NULL,operation_key TEXT NOT NULL,request_hash TEXT NOT NULL,request TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(session_id,operation_key));
            CREATE TABLE IF NOT EXISTS session_evidence(session_id TEXT NOT NULL,document_id TEXT NOT NULL,fragment TEXT NOT NULL,PRIMARY KEY(session_id,document_id,fragment));
            ''')

    def create(self,snapshot_id,*,provider,model,tool_limit=30,parent_id=None,request_id=None):
        self.store.replay(snapshot_id)
        if not all(isinstance(v,str) and 1<=len(v.strip())<=200 for v in (provider,model)) or type(tool_limit) is not int or not 1<=tool_limit<=1000:
            raise SimError('invalid_session','Declare provider, model and a tool limit from 1 to 1000.')
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=200):
            raise SimError('invalid_operation','Request ID must contain 1–200 characters.')
        request_hash=digest(encoded({'snapshot':snapshot_id,'provider':provider,'model':model,'limit':tool_limit,'parent':parent_id}))
        sid=uuid.uuid4().hex
        with self.store.db(write=True) as db:
            previous=db.execute('SELECT * FROM session_creates WHERE request_id=?',(request_id,)).fetchone() if request_id else None
            if previous:
                if previous['request_hash']!=request_hash: raise SimError('operation_conflict','Session request ID reused with different input.',409)
                sid=previous['session_id']
            else:
                db.execute('INSERT INTO agent_sessions VALUES(?,?,?,?,?,?,?,NULL,NULL)',(sid,snapshot_id,provider,model,tool_limit,self.store.clock(),parent_id))
                if request_id: db.execute('INSERT INTO session_creates VALUES(?,?,?)',(request_id,request_hash,sid))
        return self.get(sid)

    def _row(self,db,session_id):
        identifier(session_id)
        row=db.execute('SELECT * FROM agent_sessions WHERE id=?',(session_id,)).fetchone()
        if not row: raise SimError('session_not_found','Session not found.',404)
        return dict(row)

    def get(self,session_id):
        with self.store.db() as db:
            row=self._row(db,session_id)
            row['tool_calls']=db.execute('SELECT count(*) FROM session_tools WHERE session_id=?',(session_id,)).fetchone()[0]
        row['isolation']='tool_scoped_external_client_not_network_isolated'
        row['model_identity']='declared_unverified'
        return row

    def reset(self,session_id,request_id=None):
        row=self.get(session_id)
        return self.create(row['snapshot_id'],provider=row['provider'],model=row['model'],tool_limit=row['tool_limit'],parent_id=session_id,request_id=request_id)

    def tool(self,session_id,operation_key,action,**arguments):
        if not isinstance(operation_key,str) or not 1<=len(operation_key)<=200:
            raise SimError('invalid_operation','Provide a stable operation key.')
        request={'action':action,'arguments':arguments}
        request_raw=encoded(request)
        if len(request_raw)>16384: raise SimError('request_limit','Tool request exceeds 16 KiB.')
        request_hash=digest(request_raw)
        # All execution is local, bounded and read-only. One SQLite write lock
        # makes admission, results, evidence and budget atomic even on restart.
        with self.store.db(write=True) as db:
            row=self._row(db,session_id)
            prior=db.execute('SELECT * FROM session_tools WHERE session_id=? AND operation_key=?',(session_id,operation_key)).fetchone()
            if prior:
                if prior['request_hash']!=request_hash: raise SimError('operation_conflict','Operation key was used with different input.',409)
                return json.loads(prior['response'])
            if row['run_id']: raise SimError('session_submitted','Session already submitted.',409)
            used=db.execute('SELECT count(*) FROM session_tools WHERE session_id=?',(session_id,)).fetchone()[0]
            if used>=row['tool_limit']: raise SimError('tool_budget_exhausted','Session tool limit exhausted.',409)
            fragments=[]
            try:
                replay=self.store.replay(row['snapshot_id'])
                if action in ('open','find') and not isinstance(arguments.get('document_id'),str):
                    raise SimError('invalid_id','Document ID must be a string.')
                if action=='search':
                    value={'results':replay.search(arguments.get('query'),limit=arguments.get('limit',5))}
                elif action=='open':
                    doc=replay.read(arguments.get('document_id'), arguments.get('offset',0), arguments.get('max_chars',16000))
                    visible=doc['text']
                    value=doc
                    fragments=[(doc['id'],visible)]
                elif action=='find':
                    did=arguments.get('document_id')
                    matches=replay.find(did,arguments.get('query'))
                    value={'matches':matches}
                    fragments=[(did,m['context']) for m in matches]
                elif action=='follow':
                    source=arguments.get('document_id'); url=arguments.get('url')
                    if not isinstance(source,str) or not isinstance(url,str): raise SimError('invalid_follow','Provide source document and exact link URL.')
                    evidence=db.execute('SELECT 1 FROM session_evidence WHERE session_id=? AND document_id=?',(session_id,source)).fetchone()
                    if not evidence: raise SimError('source_not_opened','Open or find the source before following its links.')
                    doc=replay.open(source)
                    if url not in doc['links']: raise SimError('link_not_in_source','URL is not a link in the source.')
                    candidates=[r for r in replay.records.values() if r['url']==url]
                    if not candidates: raise SimError('archive_miss','Linked URL is absent from this snapshot.',404)
                    target=max(candidates,key=lambda r:(timestamp(r['captured_at']),timestamp(r['archived_at']),r['id']))
                    doc=replay.read(target['id']); visible=doc['text']
                    value=doc
                    fragments=[(doc['id'],visible)]
                else: raise SimError('unknown_action','Choose search, open, find or follow.')
                result={'ok':True,'result':value,'snapshot_id':row['snapshot_id']}
            except SimError as exc:
                result={'ok':False,'error':{'code':exc.code,'message':exc.message},'snapshot_id':row['snapshot_id']}
            response=encoded(result)
            db.execute('INSERT INTO session_tools VALUES(?,?,?,?,?)',(session_id,operation_key,request_hash,request_raw.decode(),response.decode()))
            for did,fragment in fragments:
                db.execute('INSERT OR IGNORE INTO session_evidence VALUES(?,?,?)',(session_id,did,fragment))
            return result

    def submit(self,session_id,*,answer,citations):
        if not isinstance(answer,str) or not 1<=len(answer.strip())<=100000 or not isinstance(citations,list) or len(citations)>100:
            raise SimError('invalid_answer','Provide a nonempty answer and at most 100 citations.')
        submission=encoded({'answer':answer,'citations':citations})
        if len(submission)>256*1024: raise SimError('submission_limit','Answer and citations exceed 256 KiB.')
        submission_hash=digest(submission)
        with self.store.db(write=True) as db:
            row=self._row(db,session_id)
            if row['run_id']:
                if row['submission_hash']!=submission_hash: raise SimError('submission_conflict','Session already has a different immutable answer.',409)
                result=db.execute('SELECT data FROM runs WHERE id=?',(row['run_id'],)).fetchone()
                return json.loads(result[0])
            replay=self.store.replay(row['snapshot_id'])
            evidence={}
            for item in db.execute('SELECT document_id,fragment FROM session_evidence WHERE session_id=?',(session_id,)):
                evidence.setdefault(item['document_id'],[]).append(item['fragment'])
            errors=[]
            for i,c in enumerate(citations):
                if not isinstance(c,dict) or not isinstance(c.get('document_id'),str) or not isinstance(c.get('quote'),str) or not c['quote'].strip() or not any(c['quote'] in fragment for fragment in evidence.get(c['document_id'],[])):
                    errors.append({'index':i,'reason':'quote_not_in_returned_evidence'})
            trace=[{'operation_key':r['operation_key'],'action':json.loads(r['request']),'observation':json.loads(r['response'])} for r in db.execute('SELECT * FROM session_tools WHERE session_id=? ORDER BY rowid',(session_id,))]
            run={'id':uuid.uuid4().hex,'world_id':replay.manifest['world_id'],'snapshot_id':row['snapshot_id'],
                 'created_at':self.store.clock(),'session_id':session_id,'answer':answer,'citations':citations,
                 'status':'invalid_citations' if errors else 'completed','citation_errors':errors,'model':row['model'],
                 'provider':row['provider'],'observed_models':[],'usage':[],'trace':trace,'cost_usd':None,
                 'isolation':'tool_scoped_external_client_not_network_isolated','model_identity':'declared_unverified',
                 'evaluation':'Exact quote visibility only; no semantic support or external-agent identity verification.'}
            # Same schema as Store.save_run, one shared transaction is required
            # so crash or concurrent submit cannot create a duplicate orphan run.
            db.execute('INSERT INTO runs VALUES(?,?,?,?)',(run['id'],run['world_id'],run['snapshot_id'],encoded(run).decode()))
            db.execute('UPDATE agent_sessions SET submission_hash=?,run_id=? WHERE id=?',(submission_hash,run['id'],session_id))
            return run
