"""Three real-time slices of a local HTTP fixture and an actual offline browser.

The loopback transport exists only in this test. No public capture safety changes,
LLM calls, paid search calls, or user credentials are involved.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime,timezone
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
import pytest
from web_research_sim.capture import CapturedPage,CaptureError,_extract
from web_research_sim.core import Store,encoded
from web_research_sim.workflow import Workflow
from web_research_sim.render_archive import capture_rendered
from web_research_sim.bundles import export_snapshot,import_snapshot
from test_pdf_capture import pdf

ORIGIN='https://controlled.test'


def resources(phase):
    value=phase*10
    table=(f'<html><head><meta name="date" content="2000-01-01"></head><body><table><tr><th>Phase</th><th>Value</th></tr><tr><td>{phase}</td><td>{value}</td></tr></table></body></html>').encode()
    dynamic=b'<html><body><p id="value">pending</p><script src="/app.js"></script>'
    if phase==2: dynamic+=b'<script src="/encoded.js"></script><script src="/missing.js"></script>'
    dynamic+=b'</body></html>'
    return {
        '/table':(200,'text/html',table,{}),
        '/report.pdf':(200,'application/pdf',pdf(f'Phase {phase}: measured value {value}.'),{}),
        '/dynamic':(200,'text/html',dynamic,{}),
        '/app.js':(200,'application/javascript',b'fetch("/data.json").then(r=>r.json()).then(v=>document.querySelector("#value").textContent="Rendered value "+v.value)',{}),
        '/data.json':(200,'application/json',json.dumps({'value':value}).encode(),{}),
        '/encoded.js':(200,'application/javascript',gzip.compress(b'window.unusedFixture=true;',mtime=0),{'content-encoding':'gzip'}),
        '/missing.js':(404,'text/plain',b'Fixture asset intentionally missing',{})}


@contextmanager
def site():
    state={'phase':1,'requests':[]}
    all_resources={n:resources(n) for n in (1,2,3)}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            phase=state['phase'];state['requests'].append({'phase':phase,'path':self.path})
            status,kind,body,extra=all_resources[phase].get(self.path,(404,'text/plain',b'not found',{}))
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            for k,v in extra.items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    port=server.server_address[1]
    def fetch(url,*,max_bytes=2*1024**2,timeout_seconds=20):
        parsed=urlsplit(url)
        # This explicit mapping is test-only, fixed origin and fixed server port.
        # Browser itself remains network-none; source HTTP happens on the host.
        if parsed.scheme!='https' or parsed.netloc!='controlled.test' or parsed.query or parsed.fragment:
            raise CaptureError('outside_controlled_fixture')
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=min(20,timeout_seconds))
        try:
            connection.request('GET',parsed.path or '/')
            response=connection.getresponse();body=response.read(max_bytes+1)
            if len(body)>max_bytes:raise CaptureError('fixture_byte_limit')
            return {'url':url,'requested_url':url,'body':body,'status':response.status,
                    'headers':{k.lower():v for k,v in response.getheaders()},
                    'captured_at':datetime.now(timezone.utc).isoformat()}
        finally:connection.close()
    try:yield state,all_resources,fetch
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_three_slice_http_pdf_and_real_browser_archive_matrix(tmp_path):
    image=os.environ.get('RENDER_TEST_IMAGE')
    if not image:pytest.skip('Explicit immutable Docker renderer image required')
    store=Store(tmp_path/'source');world=store.create_world('Controlled temporal collection matrix')['id']
    captured=[];snapshots=[];observations=[]
    with site() as (state,expected,fetch):
        def static_fetch(url):
            response=fetch(url)
            metadata={k:v for k,v in response.items() if k not in ('body','headers')}
            metadata['content_type']=response['headers']['content-type']
            receipt=store.preserve_original(world,response['body'],metadata)
            text,title,links=_extract(response['body'],response['headers']['content-type'],url)
            return CapturedPage(url,url,response['body'],text,title,response['status'],response['headers'],
                                response['captured_at'],links,extractor='controlled_http_actual_static_extractor',original_response_id=receipt)
        workflow=Workflow(store,fetch=static_fetch)
        for phase in (1,2,3):
            state['phase']=phase
            result=workflow.capture_discovered(world,f'phase-{phase}-static',sources=[{'url':ORIGIN+'/table'},{'url':ORIGIN+'/report.pdf'}],native_client='self-authored controlled HTTP fixture, not native search')
            assert result['captured']==2 and result['failed']==0
            rendered=capture_rendered(store,world,ORIGIN+'/dynamic',operation_key=f'phase-{phase}-rendered',image_id=image,fetch_response=fetch)
            assert store.objects.get(rendered['text_sha256'])==f'Rendered value {phase*10}'.encode()
            if phase==2:
                assert rendered['extraction_status']=='partial'
                assert 'encoded_response_unsupported' in {x['reason'] for x in rendered['provenance']['blocked_requests']}
                assert any(x['reason']=='http_error' and x['status']==404 for x in rendered['provenance']['blocked_requests'])
            else:assert rendered['extraction_status']=='completed'
            phase_records=[r for r in store.captures(world) if r['id'] in result['document_ids']+[rendered['id']]]
            assert len(phase_records)==3
            for record in phase_records:
                path=urlsplit(record['url']).path
                assert store.objects.get(record['body_sha256'])==expected[phase][path][2]
                if path=='/report.pdf':assert f'Phase {phase}: measured value {phase*10}.' in store.objects.get(record['text_sha256']).decode()
                if path=='/table':
                    text=store.objects.get(record['text_sha256']).decode()
                    assert 'Phase' in text and 'Value' in text and str(phase*10) in text
            for artifact in rendered['artifacts']:
                body=store.objects.get(artifact['sha256'])
                assert len(body)==artifact['bytes']
                if artifact['kind']=='source_asset':assert body==expected[phase][urlsplit(artifact['url']).path][2]
                else:assert artifact['kind']=='derived_dom' and f'Rendered value {phase*10}'.encode() in body
            captured.append(phase_records)
            snapshot=store.freeze(world);snapshots.append(snapshot)
            assert snapshot['count']==phase*3
            observations.append({'phase':phase,'snapshot_id':snapshot['id'],'cutoff':snapshot['cutoff'],
                'records':len(phase_records),'rendered_status':rendered['extraction_status'],
                'rendered_asset_count':len(rendered['artifacts']),'browser_version':rendered['provenance']['browser_version']})
        # Earlier publication text cannot leak later captures into an old view.
        for phase,snapshot in enumerate(snapshots,1):
            replay=store.replay(snapshot['id'])
            expected_ids={r['id'] for group in captured[:phase] for r in group}
            assert set(replay.records)==expected_ids
            assert all(r['id'] not in replay.records for group in captured[phase:] for r in group)
            latest={hit['url']:hit for hit in replay.search('value',limit=20)}
            assert latest[ORIGIN+'/table']['body_sha256']==hashlib.sha256(expected[phase]['/table'][2]).hexdigest()
            assert latest[ORIGIN+'/dynamic']['snippet']==f'Rendered value {phase*10}'
            assert f'Phase {phase}: measured value {phase*10}.' in latest[ORIGIN+'/report.pdf']['snippet']
            destination=Store(tmp_path/f'import-{phase}')
            import_snapshot(destination,export_snapshot(store,snapshot['id'],redistribution_acknowledged=True))
            restored=destination.replay(snapshot['id'])
            for did in expected_ids:assert encoded(restored.open(did))==encoded(replay.open(did))
            assert restored.search('value')==replay.search('value')
        # Missing HTTP asset original/status remain durable alongside an explicit gap.
        with store.db() as db:
            originals=[dict(r) for r in db.execute('SELECT * FROM original_responses WHERE world_id=?',(world,))]
        missing=[r for r in originals if json.loads(r['metadata'])['url']==ORIGIN+'/missing.js']
        assert len(missing)==1 and json.loads(missing[0]['metadata'])['status']==404
        assert store.objects.get(missing[0]['body_hash'])==expected[2]['/missing.js'][2]
        assert any(r['path']=='/report.pdf' for r in state['requests'])
        receipt={'schema':'controlled-temporal-matrix-v1','mode':'actual_local_HTTP_site_and_actual_network_none_Docker_browser',
                 'public_network_used':False,'source_transport':'test_only_fixed_origin_to_loopback_mapping',
                 'image_id':image,'slices':observations,'http_requests':state['requests'],
                 'verified':['exact HTML/table originals','real PDF extraction and originals','real browser JS/JSON and assets','three real capture-time cutoffs','no future version in earlier views','all snapshot object bytes and artifact bytes','all three export/import roundtrips','unsupported encoded asset explicit gap','missing 404 asset preserved and classified as gap'],
                 'limitations':['Loopback adapter is test-only; does not certify public fetch DNS behavior.','No real 24-hour soak or independent human validation implied.']}
        (tmp_path/'temporal-matrix-receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
        output=os.environ.get('TEMPORAL_RECEIPT_PATH')
        if output:Path(output).write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
