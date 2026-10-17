"""Durable bounded collection schedules. Uncertain slots are never redispatched."""
from datetime import timedelta
import json
import uuid
from .core import SimError, encoded, digest, identifier, timestamp, now
from .workflow import Workflow


class ScheduleManager:
    def __init__(self,store,workflow=None,clock=now,owner_id='api-scheduler'):
        if not isinstance(owner_id,str) or not 1<=len(owner_id)<=200: raise ValueError('Explicit bounded owner namespace required')
        self.store,self.workflow,self.clock,self.owner_id=store,workflow or Workflow(store),clock,owner_id
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,owner_id TEXT NOT NULL,kind TEXT NOT NULL,config TEXT NOT NULL,interval_seconds INTEGER NOT NULL,max_runs INTEGER NOT NULL,start_at TEXT NOT NULL,next_index INTEGER NOT NULL,status TEXT NOT NULL,skipped_count INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS schedule_slots(schedule_id TEXT NOT NULL,slot_index INTEGER NOT NULL,due_at TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,owner_id TEXT NOT NULL,status TEXT NOT NULL,result TEXT,PRIMARY KEY(schedule_id,slot_index));
            ''')

    def create(self,world_id,kind,config,interval_seconds,max_runs,start_at,request_id):
        self.store.world(world_id)
        start_at=timestamp(start_at).isoformat()
        if type(interval_seconds) is not int or not 1<=interval_seconds<=31*86400 or type(max_runs) is not int or not 1<=max_runs<=10000:
            raise SimError('invalid_schedule','Use positive interval seconds and 1–10000 scheduled slots.')
        if not isinstance(request_id,str) or not 1<=len(request_id)<=200 or not isinstance(config,dict):
            raise SimError('invalid_schedule','Provide config and a stable request ID.')
        allowed={'collection':{'model_provider','model','search_provider','max_queries','max_pages'},'native_discovered':{'sources','native_client'}}
        required={'collection':{'model_provider','model','search_provider'},'native_discovered':{'sources','native_client'}}
        if kind not in allowed or not required[kind]<=set(config)<=allowed[kind]:
            raise SimError('invalid_schedule','Unsupported collection configuration.')
        if kind=='collection' and (any(not isinstance(config[k],str) or not config[k].strip() for k in required[kind]) or type(config.get('max_queries',3)) is not int or not 1<=config.get('max_queries',3)<=10 or type(config.get('max_pages',5)) is not int or not 1<=config.get('max_pages',5)<=50):
            raise SimError('invalid_schedule','Invalid model, search or collection limits.')
        if kind=='native_discovered' and (not isinstance(config['native_client'],str) or not config['native_client'].strip() or not isinstance(config['sources'],list) or not 1<=len(config['sources'])<=20 or any(not isinstance(s,dict) or not isinstance(s.get('url'),str) or not s['url'].startswith(('http://','https://')) for s in config['sources'])):
            raise SimError('invalid_schedule','Provide 1–20 source URLs and native client identity.')
        raw=encoded(config)
        if len(raw)>65536: raise SimError('invalid_schedule','Schedule config exceeds 64 KiB.')
        signature=digest(encoded([world_id,self.owner_id,kind,config,interval_seconds,max_runs,start_at]))
        sid=uuid.uuid4().hex
        with self.store.db(write=True) as db:
            prior=db.execute('SELECT id,request_hash FROM schedules WHERE request_id=?',(request_id,)).fetchone()
            if prior:
                if prior['request_hash']!=signature: raise SimError('operation_conflict','Schedule request ID used with different input.',409)
                sid=prior['id']
            else:
                db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,0,?,0,?,?)',(sid,world_id,self.owner_id,kind,raw.decode(),interval_seconds,max_runs,start_at,'active',request_id,signature))
        return self.status(sid)

    def _row(self,db,sid):
        identifier(sid)
        row=db.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone()
        if not row: raise SimError('schedule_not_found','Schedule not found.',404)
        if row['owner_id']!=self.owner_id: raise SimError('schedule_owner','Schedule belongs to a different runner.',409)
        return dict(row)

    def status(self,schedule_id):
        with self.store.db() as db:
            row=self._row(db,schedule_id)
            row['slots']=[{**dict(s),'result':json.loads(s['result']) if s['result'] else None} for s in db.execute('SELECT * FROM schedule_slots WHERE schedule_id=? ORDER BY slot_index',(schedule_id,))]
        row['config']=json.loads(row['config'])
        row['next_due_at']=(timestamp(row['start_at'])+timedelta(seconds=row['interval_seconds']*row['next_index'])).isoformat() if row['next_index']<row['max_runs'] else None
        return row

    def list(self,world_id=None):
        with self.store.db() as db:
            rows=db.execute('SELECT id FROM schedules WHERE owner_id=?'+(' AND world_id=?' if world_id else '')+' ORDER BY start_at,id',(self.owner_id,world_id) if world_id else (self.owner_id,)).fetchall()
        return [self.status(r['id']) for r in rows]

    def pause(self,schedule_id):
        with self.store.db(write=True) as db:
            row=self._row(db,schedule_id)
            if row['status']!='completed': db.execute("UPDATE schedules SET status='paused' WHERE id=?",(schedule_id,))
        return self.status(schedule_id)

    def resume(self,schedule_id):
        with self.store.db(write=True) as db:
            row=self._row(db,schedule_id)
            if row['status']!='completed': db.execute("UPDATE schedules SET status='active' WHERE id=?",(schedule_id,))
        return self.status(schedule_id)

    def recover_owned(self,*,exclusive_owner_confirmed=False):
        """Caller must hold lifetime exclusive lock proving prior runner stopped.

        Never infer death from elapsed time. Only this namespace is recovered.
        """
        if exclusive_owner_confirmed is not True:
            raise SimError('exclusive_owner_required','Verify prior runner stopped and hold its exclusive process lock.')
        with self.store.db(write=True) as db:
            rows=db.execute("SELECT schedule_id,slot_index,job_id FROM schedule_slots WHERE owner_id=? AND status='claimed'",(self.owner_id,)).fetchall()
            db.execute("UPDATE schedule_slots SET status='interrupted',result=? WHERE owner_id=? AND status='claimed'",(encoded({'error':'owner_stopped_outcome_uncertain','resubmitted':False}).decode(),self.owner_id))
            db.execute("UPDATE schedules SET status='completed' WHERE owner_id=? AND next_index>=max_runs AND NOT EXISTS (SELECT 1 FROM schedule_slots WHERE schedule_id=schedules.id AND status='claimed')",(self.owner_id,))
        return [dict(r) for r in rows]

    def tick(self):
        current=timestamp(self.clock()); claimed=None
        with self.store.db(write=True) as db:
            rows=db.execute("SELECT * FROM schedules WHERE owner_id=? AND status='active' ORDER BY start_at,id",(self.owner_id,)).fetchall()
            for raw in rows:
                row=dict(raw); sid=row['id']
                # Any previously claimed slot blocks overlap, regardless of age.
                if db.execute("SELECT 1 FROM schedule_slots WHERE schedule_id=? AND status='claimed'",(sid,)).fetchone(): continue
                start=timestamp(row['start_at']); index=row['next_index']
                if index>=row['max_runs']:
                    db.execute("UPDATE schedules SET status='completed' WHERE id=?",(sid,)); continue
                latest=int((current-start).total_seconds()//row['interval_seconds'])
                if latest<index: continue
                # Do not replay missed historical slots; after the final due slot
                # is an interval old, the entire schedule is missed/complete.
                if latest>=row['max_runs']:
                    skipped=row['max_runs']-index
                    db.execute("UPDATE schedules SET next_index=max_runs,skipped_count=skipped_count+?,status='completed' WHERE id=?",(skipped,sid)); continue
                skipped=latest-index
                due=(start+timedelta(seconds=latest*row['interval_seconds'])).isoformat()
                job=uuid.uuid4().hex
                db.execute('INSERT INTO schedule_slots VALUES(?,?,?,?,?,?,NULL)',(sid,latest,due,job,self.owner_id,'claimed'))
                db.execute('UPDATE schedules SET next_index=?,skipped_count=skipped_count+? WHERE id=?',(latest+1,skipped,sid))
                claimed=(row,latest,job,due)
                break
        if claimed is None: return []
        row,index,job,due=claimed
        try:
            config=json.loads(row['config'])
            if row['kind']=='collection': result=self.workflow.collect(row['world_id'],job,**config)
            else: result=self.workflow.capture_discovered(row['world_id'],job,**config)
            state='completed'
        except Exception as exc:
            result={'error_type':type(exc).__name__,'error_code':exc.code if isinstance(exc,SimError) else None,'resubmitted':False}
            state='uncertain'
        # A process death before this transaction leaves claimed, never retryable.
        with self.store.db(write=True) as db:
            cursor=db.execute("UPDATE schedule_slots SET status=?,result=? WHERE schedule_id=? AND slot_index=? AND status='claimed'",(state,encoded(result).decode(),row['id'],index))
            if cursor.rowcount!=1: raise SimError('slot_conflict','Slot was recovered or finalized concurrently.',409)
            db.execute("UPDATE schedules SET status='completed' WHERE id=? AND next_index>=max_runs",(row['id'],))
        return [{'schedule_id':row['id'],'slot_index':index,'due_at':due,'job_id':job,'status':state,'result':result}]
