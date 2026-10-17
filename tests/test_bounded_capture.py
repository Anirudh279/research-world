"""Fresh real processes; synthetic transport only, no external requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from web_research_sim import bounded_capture as b
from web_research_sim.capture import CaptureError
from web_research_sim.core import Store, SimError

FIXTURE = '''
from web_research_sim import capture_worker as w, capture as c
class Response:
    status=200
    def getheaders(self):return [('content-type','text/html')]
    def read(self,n):return b'<title>Fixture</title><p>Saved before extraction.</p>'
class Connection:
    def __init__(self,*a):pass
    def request(self,*a,**k):pass
    def getresponse(self):return Response()
    def close(self):pass
c._public_addresses=lambda *a:[None]
c._PinnedConnection=Connection
'''


def fixture_worker(monkeypatch, extra=''):
    runner=b._run_worker
    def run(command, request, **kwargs):
        assert command[1:]==['-m','web_research_sim.capture_worker']
        assert not any(k in kwargs['env'] for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','HTTP_PROXY','HOME'))
        return runner([sys.executable,'-c',FIXTURE+extra+'\nw.main()'],request,**kwargs)
    monkeypatch.setattr(b,'_run_worker',run)


def test_real_worker_private_address(tmp_path):
    store=Store(tmp_path);world=store.create_world('Private address fixture')
    with pytest.raises(CaptureError,match='private_destination'):
        b.fetch_bounded(store,world['id'],'http://127.0.0.1/',timeout_seconds=5)


def test_fresh_worker_preserves_and_extracts(tmp_path,monkeypatch):
    store=Store(tmp_path);world=store.create_world('Offline fixture')
    monkeypatch.setenv('OPENAI_API_KEY','DO_NOT_FORWARD')
    fixture_worker(monkeypatch)
    page=b.fetch_bounded(store,world['id'],'https://example.org/',timeout_seconds=5)
    assert page.text=='Fixture\nSaved before extraction.'
    assert page.title=='Fixture' and page.extraction_status=='completed'
    with store.db() as db:row=db.execute('SELECT * FROM original_responses WHERE id=?',(page.original_response_id,)).fetchone()
    assert row['world_id']==world['id']
    assert store.objects.get(row['body_hash'])==page.body


def test_original_durable_before_extraction_timeout_and_reservation_spent(tmp_path,monkeypatch):
    store=Store(tmp_path);world=store.create_world('Timeout fixture',{'model_calls':0,'search_calls':0,'capture_calls':1})
    fixture_worker(monkeypatch,'\nimport time\ndef hang(*a):time.sleep(60)\nc._extract=hang\n')
    store.reserve(world['id'],'capture_calls','one',{'url':'https://example.org/'})
    with pytest.raises(CaptureError,match='capture_timeout'):
        b.fetch_bounded(store,world['id'],'https://example.org/',timeout_seconds=1)
    with store.db() as db:
        rows=db.execute('SELECT * FROM original_responses').fetchall()
        assert db.execute('SELECT status FROM operations').fetchone()[0]=='uncertain'
    assert len(rows)==1 and b'Saved before extraction' in store.objects.get(rows[0]['body_hash'])
    with pytest.raises(SimError,match='already reserved'):store.reserve(world['id'],'capture_calls','one',{'url':'https://example.org/'})
    with pytest.raises(SimError,match='exhausted'):store.reserve(world['id'],'capture_calls','two',{})


def running(pid):
    if sys.platform.startswith('linux'):
        try:
            state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
            return state not in {'Z','X'}
        except FileNotFoundError:
            return False
    result=subprocess.run(['ps','-o','stat=','-p',str(pid)],capture_output=True,text=True)
    return bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


def test_deadline_kills_worker_and_grandchild(tmp_path):
    receipt=tmp_path/'pids.json'
    code=f'''import subprocess,sys,os,json,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
open({str(receipt)!r},'w').write(json.dumps([os.getpid(),child.pid]))
time.sleep(60)
'''
    start=time.monotonic()
    with pytest.raises(CaptureError,match='capture_timeout'):
        b._run_worker([sys.executable,'-c',code],b'{}',timeout_seconds=1)
    assert time.monotonic()-start<3
    pids=json.loads(receipt.read_text())
    for _ in range(30):
        if not any(running(pid) for pid in pids):break
        time.sleep(.02)
    assert not any(running(pid) for pid in pids)


def test_output_bound():
    with pytest.raises(CaptureError,match='response_too_large'):
        b._run_worker([sys.executable,'-c',"import sys;sys.stdin.read();sys.stdout.write('x'*10000)"],b'{}',timeout_seconds=3,max_output_bytes=100)


def test_raw_error_redacted(tmp_path,monkeypatch):
    store=Store(tmp_path);world=store.create_world('Error fixture')
    fixture_worker(monkeypatch,"\ndef secret(*a,**k):raise RuntimeError('SECRET-CREDENTIAL')\nw.fetch_page=secret\n")
    with pytest.raises(CaptureError,match='^capture_failed$'):
        b.fetch_bounded(store,world['id'],'https://example.org/',timeout_seconds=5)


def test_invalid_worker_input_bound():
    with pytest.raises(CaptureError,match='capture_failed'):
        b._run_worker(['never-executed'],b'x'*(b.MAX_INPUT+1),timeout_seconds=1)


def test_workflow_default_uses_bounded_worker(tmp_path, monkeypatch):
    from web_research_sim import workflow
    store = Store(tmp_path)
    sentinel = object()
    calls = []
    def bounded(*args):
        calls.append(args)
        return sentinel
    monkeypatch.setattr(workflow, 'fetch_bounded', bounded)
    assert workflow.Workflow(store).fetch_preserved('world', 'https://example.org') is sentinel
    assert calls == [(store, 'world', 'https://example.org')]
