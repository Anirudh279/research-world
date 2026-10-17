"""Trusted-host broker for network-disabled, manifest-only Docker tool execution."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid
from .core import SimError,Objects,Replay,artifact_references,encoded
from .bounded_capture import _run_worker


def docker(*args,timeout=15):
    try:
        r=subprocess.run(['docker',*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout)
        if r.returncode or len(r.stdout)>1024*1024:raise ValueError()
        return r.stdout
    except (OSError,ValueError,subprocess.TimeoutExpired):raise SimError('isolation_failed','Docker isolation operation failed.') from None


class IsolatedReplay:
    isolation='docker_frozen_tools_network_none; model_api_and_trusted_host_outside_boundary'
    def __init__(self,objects,snapshot_id,*,image_id,timeout_seconds=15,max_stage_bytes=256*1024**2):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id):raise SimError('invalid_image','An immutable Docker image ID is required.')
        if type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=60:raise SimError('invalid_timeout','Set a tool deadline at most 60 seconds.')
        if type(max_stage_bytes) is not int or not 1<=max_stage_bytes<=1024**3:
            raise SimError('invalid_limits','Set an integer stage byte limit from 1 through 1 GiB.')
        self.boundary_receipt=[]
        self.image_id=image_id;self.snapshot_id=snapshot_id;self.timeout=timeout_seconds
        self.temp=tempfile.TemporaryDirectory(prefix='frozen-replay-');self.root=Path(self.temp.name);self.closed=False
        try:
            objects=getattr(objects,"objects",objects)
            host=Replay(objects,snapshot_id);self.manifest=host.manifest
            hashes={snapshot_id}
            for record in host.records.values():
                hashes.update((record['body_sha256'],record['text_sha256']))
                hashes.update(a['sha256'] for a in artifact_references(record))
            staged=Objects(self.root);total=0
            for sha in sorted(hashes):
                raw=objects.get(sha);total+=len(raw)
                if total>max_stage_bytes:raise SimError('stage_limit','Frozen archive exceeds the configured stage limit.')
                if staged.put(raw)!=sha:raise SimError('integrity_error','Frozen stage hash mismatch.')
            replay=Replay(staged,snapshot_id)
            for did in replay.records:replay.open(did)
            os.chmod(self.root,0o755)
            for file in self.root.iterdir():file.chmod(0o444)
            self.allowed_hashes=frozenset(hashes)
            self._call("ready")  # Verify actual Docker execution before model admission.
        except BaseException:self.close();raise

    def close(self):
        if not self.closed:self.temp.cleanup();self.closed=True

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def _inspect(self,name):
        try:info=json.loads(docker('inspect',name))[0]
        except (ValueError,IndexError,KeyError):raise SimError('isolation_failed','Cannot inspect frozen container.') from None
        h=info['HostConfig'];c=info['Config'];mounts=info['Mounts']
        if not (info['Image']==self.image_id and h['NetworkMode']=='none' and h['ReadonlyRootfs']
            and h['Memory']==512*1024**2 and h['MemorySwap']==512*1024**2 and h['NanoCpus']==1000000000
            and h['PidsLimit']==64 and not h['Privileged'] and c['User']=='10001:10001'
            and 'ALL' in h['CapDrop'] and any(x.startswith('no-new-privileges') for x in h['SecurityOpt'])
            and len(mounts)==1 and mounts[0]['Source']==str(self.root) and mounts[0]['Destination']=='/archive'
            and not mounts[0]['RW']):raise SimError('isolation_failed','Frozen container settings do not match required isolation.')
        return info

    def _call(self,tool,**args):
        if self.closed:raise SimError('closed','Frozen replay is closed.')
        request=encoded({'tool':tool,'args':args})
        if len(request)>16384:raise SimError('request_limit','Tool request exceeds its limit.')
        name='wrs-frozen-'+uuid.uuid4().hex
        try:
            docker('create','--name',name,'--network','none','--read-only','--user','10001:10001',
                   '--cpus','1','--memory','512m','--memory-swap','512m','--pids-limit','64',
                   '--cap-drop','ALL','--security-opt','no-new-privileges',
                   '--mount',f'type=bind,src={self.root},dst=/archive,readonly','--interactive',
                   self.image_id,self.snapshot_id)
            self._inspect(name)  # Created immutable configuration inspected before execution.
            self.boundary_receipt.append({'image_id':self.image_id,'snapshot_id':self.snapshot_id,
                'network':'none','readonly':True,'user':'10001:10001','object_count':len(self.allowed_hashes),
                'memory_bytes':512*1024**2,'cpus':1,'pids':64,'tool':tool})
            raw=_run_worker(['docker','start','--attach','--interactive',name],request,
                            timeout_seconds=self.timeout,max_output_bytes=256*1024)
            response=json.loads(raw)
            if 'error' in response:
                error=response['error'];raise SimError(error['code'],error['message'])
            if response.get('snapshot_id')!=self.snapshot_id:raise SimError('integrity_error','Worker snapshot mismatch.')
            return response['result']
        except SimError:raise
        except Exception:raise SimError('worker_failed','Frozen tool failed or timed out; no fallback.') from None
        finally:docker('rm','--force',name,timeout=10)

    def search(self,query,limit=10):return self._call('search',query=query,limit=limit)
    def read(self,document_id,offset=0,max_chars=16000):return self._call('open',document_id=document_id,offset=offset,max_chars=max_chars)
    def find(self,document_id,query):return self._call('find',document_id=document_id,query=query)
    def follow(self,document_id,url):return self._call('follow',document_id=document_id,url=url)
