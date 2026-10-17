"""Dashboard transport unit checks; not a substitute for browser visual QA."""
import json
from pathlib import Path
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient
from web_research_sim.api import create_app


STATIC = Path(__file__).parents[1] / 'src/web_research_sim/static'


def test_dashboard_public_shell_but_data_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv('WR_SIM_TOKEN', 'test-workspace-token')
    monkeypatch.delenv('WR_SIM_PUBLIC_HOST', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        shell = client.get('/')
        assert shell.status_code == 200
        assert 'workspace-token' in shell.text
        assert 'not an OpenAI or Claude credential' in shell.text
        assert 'test-workspace-token' not in shell.text
        assert client.get('/api/worlds').status_code == 401
        assert client.get('/api/worlds', headers={'Authorization': 'Bearer test-workspace-token'}).status_code == 200


def test_token_memory_only_get_and_post_disconnect(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for JavaScript behavior test')
    # Tiny injected DOM/fetch doubles exercise the real script's transport and
    # connection handlers. This is intentionally not called a browser test.
    harness = r'''
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const elements=new Map();
const make=()=>({textContent:'',value:'',hidden:false,options:[],replaceChildren(){},append(){}});
const document={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make,querySelectorAll(){return[];}};
const requests=[],persisted=[];
const sandbox={document,setTimeout,crypto:{randomUUID:()=> 'test-key'},sessionStorage:{getItem:()=>null,setItem:(...v)=>persisted.push(v),removeItem(){}},fetch:async(path,options)=>{requests.push({path,options});return{ok:true,status:200,json:async()=>path==='/api/status'?{version:'test',providers:{},limitations:[]}:path==='/api/worlds'?{worlds:[]}:path==='/api/starters'?{starters:[]}:{id:'test'}};}};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
(async()=>{
await vm.runInContext("connect('fixture-token')",sandbox);
await vm.runInContext("api('/api/worlds', {question:'fixture'})",sandbox);
assert(requests.some(r=>r.options.headers.Authorization==='Bearer fixture-token' && !r.options.method));
assert(requests.some(r=>r.options.headers.Authorization==='Bearer fixture-token' && r.options.method==='POST'));
assert.equal(persisted.length,0);
assert.equal(document.getElementById('connection').textContent,'● Connected');
document.getElementById('disconnect').onclick();
assert.equal(vm.runInContext('workspaceToken',sandbox),'');
await vm.runInContext("api('/api/status')",sandbox);
assert.equal(requests.at(-1).options.headers.Authorization,undefined);
assert.equal(document.getElementById('connection').textContent,'○ Not connected');
assert.equal(persisted.length,0);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    path = tmp_path / 'dashboard-check.cjs'
    path.write_text(harness)
    result = subprocess.run([node, str(path), str(STATIC / 'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_capabilities_disclose_partial_capture_and_isolation():
    html = (STATIC / 'index.html').read_text()
    assert 'One real end-to-end run verified' in html
    assert 'Real run verified · one capture failed' in html
    assert 'does not isolate the whole agent' in html
    assert 'Do not paste subscription credentials here' in html


def test_starter_ui_installs_selects_snapshot_without_model_submission(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for JavaScript behavior test')
    harness = r'''const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),requests=[];
const make=()=>({textContent:'',value:'',hidden:false,options:[],children:[],replaceChildren(...v){this.children=v;this.options=[];},append(...v){this.children.push(...v);this.options.push(...v);}});
const document={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make,querySelectorAll(){return[];}};
const sandbox={document,setTimeout,crypto:{randomUUID:()=> 'key'},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},fetch:async(path,options)=>{requests.push({path,options});const payload=path==='/api/status'?{version:'fixture',providers:{},limitations:[]}:path==='/api/starters'?{starters:[{slug:'river',title:'Synthetic river',question:'River?'}]}:path==='/api/starters/river'?{world_id:'world',default_snapshot_id:'day2'}:path==='/api/worlds'?{worlds:[]}:path==='/api/worlds/world'?{world:{id:'world',question:'River?'},usage:{},captures:[],runs:[],snapshots:[{id:'day1',count:2,cutoff:'2026-01-01T12:00:00Z'},{id:'day2',count:3,cutoff:'2026-01-02T12:00:00Z'}]}:{};return{ok:true,status:200,json:async()=>payload};}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
(async()=>{await vm.runInContext('starters()',sandbox);const card=document.getElementById('starter-list').children.at(-1);await card.children.at(-1).onclick();assert.equal(document.getElementById('snapshot').value,'day2');assert.equal(document.getElementById('workspace').hidden,false);assert(document.getElementById('notice').textContent.includes('No LLM has run'));const writes=requests.filter(r=>r.options.method==='POST');assert.deepEqual(writes.map(r=>r.path),['/api/starters/river']);})().catch(e=>{console.error(e);process.exitCode=1});'''
    path = tmp_path / 'starter-check.cjs'
    path.write_text(harness)
    result = subprocess.run([node, str(path), str(STATIC / 'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_capture_gap_and_evidence_check_ui_uses_text_and_get_only(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for JavaScript behavior test')
    harness = r'''const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map(),requests=[];
const make=()=>({textContent:'',value:'',hidden:false,options:[],children:[],replaceChildren(...v){this.children=v;this.options=[];},append(...v){this.children.push(...v);this.options.push(...v);},querySelector(){return null;}});
const document={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make,createTextNode:text=>({textContent:text}),querySelectorAll(){return[];}};
const world={world:{id:'world',question:'Fixture?'},usage:{},captures:[{id:'doc',url:'https://fixture.example',title:'<script>bad()</script>',status:403,extraction_status:'partial',extraction_error:'http_error'}],events:[{kind:'capture_failed',data:{url:'https://failed.example',error_code:'tls_failure',message:'TLS could not be verified.'}}],runs:[{id:'run',snapshot_id:'snap',model:'fixture',status:'completed',answer:'Answer'}],snapshots:[]};
const sandbox={document,setTimeout,crypto:{randomUUID:()=> 'key'},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},fetch:async(path,options)=>{requests.push({path,options});const data=path==='/api/status'?{version:'fixture',providers:{},limitations:[]}:path==='/api/starters'?{starters:[]}:path==='/api/worlds'?{worlds:[]}:path.endsWith('/evaluation')?{semantic_judge:'unknown'}:world;return{ok:true,status:200,json:async()=>data};}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
const text=n=>[n.textContent,...(n.children||[]).map(text)].join(' ');
(async()=>{await vm.runInContext("load('world')",sandbox);assert(text(document.getElementById('captures')).includes('<script>bad()</script>'));assert(text(document.getElementById('captures')).includes('partial'));assert(text(document.getElementById('captures')).includes('http_error'));assert(text(document.getElementById('capture-failures')).includes('tls_failure'));
vm.runInContext("showCaptureDiagnostics({captured:1,failures:[{url:'https://failed.example',error_code:'dns_failure',message:'DNS failed'}],extraction_gaps:[{document_id:'doc',error:'http_error'}]})",sandbox);
assert(text(document.getElementById('capture-diagnostics')).includes('Saved does not mean complete or usable'));assert(text(document.getElementById('capture-diagnostics')).includes('dns_failure'));assert(text(document.getElementById('capture-diagnostics')).includes('http_error'));
const card=document.getElementById('runs').children[0];const button=card.children.find(n=>n.textContent==='View evidence checks');await button.onclick();assert(requests.some(r=>r.path==='/api/runs/run/evaluation'));assert(text(card).includes('do not establish semantic support'));assert(!requests.some(r=>r.options.method==='POST'));assert(!fs.readFileSync(process.argv[2],'utf8').includes('innerHTML'));
})().catch(e=>{console.error(e);process.exitCode=1});'''
    path = tmp_path / 'gaps-check.cjs'
    path.write_text(harness)
    result = subprocess.run([node, str(path), str(STATIC / 'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_storage_status_distinguishes_backend_without_secret_or_connectivity_claim(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for JavaScript behavior test')
    harness = r'''const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const elements=new Map();const make=()=>({textContent:'',value:'',hidden:false,options:[],replaceChildren(){},append(){}});
const document={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make,querySelectorAll(){return[];}};
const sandbox={document,setTimeout,crypto:{randomUUID:()=> 'key'},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},fetch:async(path)=>({ok:true,status:200,json:async()=>path==='/api/status'?{version:'test',providers:{},storage:{backend:'s3',status:'configured_unverified',metadata:'local_sqlite',connectivity_verified:false,detail:'PRIVATE_ENDPOINT'},limitations:[]}:path==='/api/starters'?{starters:[]}:{worlds:[]}})};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
(async()=>{await vm.runInContext("connect('fixture')",sandbox);let text=document.getElementById('storage-status').textContent;assert(text.includes('S3-compatible object storage'));assert(text.includes('local SQLite'));assert(text.includes('not verified'));assert(!text.includes('PRIVATE_ENDPOINT'));vm.runInContext("showStorage({backend:'local',status:'configured_unverified',metadata:'local_sqlite',connectivity_verified:true,detail:'SECRET'})",sandbox);text=document.getElementById('storage-status').textContent;assert(text.includes('Local file storage'));assert(text.includes('not verified'));assert(!text.includes('SECRET'));vm.runInContext("showStorage({backend:'s3',status:'unavailable',metadata:'local_sqlite',detail:'PRIVATE_ENDPOINT'})",sandbox);text=document.getElementById('storage-status').textContent;assert(text.includes('configuration needs attention'));assert(text.includes('CLI preflight'));assert(!text.includes('configured.'));assert(!text.includes('PRIVATE_ENDPOINT'));vm.runInContext('showStorage(null)',sandbox);assert(document.getElementById('storage-status').textContent.includes('unavailable'));document.getElementById('disconnect').onclick();assert.equal(document.getElementById('storage-status').textContent,'Connect to inspect archive storage.');})().catch(e=>{console.error(e);process.exitCode=1});'''
    path = tmp_path / 'storage-check.cjs'
    path.write_text(harness)
    result = subprocess.run([node, str(path), str(STATIC / 'app.js')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("terminal_handles", [False, True])
def test_plan_review_requires_explicit_approval_and_invalidates_settings(tmp_path, terminal_handles):
    node = shutil.which('node')
    if not node: pytest.skip('Node required')
    harness = r'''const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const terminalHandles=process.argv[3]==='true';const elements=new Map(),requests=[],storage=new Map();const make=()=>({textContent:'',value:'',hidden:false,options:[],children:[],replaceChildren(...v){this.children=v;this.options=[];},append(...v){this.children.push(...v);this.options.push(...v);}});
const document={getElementById(id){if(!elements.has(id))elements.set(id,make());return elements.get(id);},createElement:make,querySelectorAll(){return[];}};
const config={model_provider:'openai',model:'fixture',search_provider:'brave',max_queries:3,max_pages:5};
const world={world:{id:'world',question:'Fixture?'},captures:[],runs:[],snapshots:[],usage:{},events:[]};
const sandbox={document,setTimeout,crypto:{randomUUID:()=> 'stable'},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch:async(path,options)=>{requests.push({path,options});const result=path==='/api/status'?{version:'fixture',providers:{},limitations:[]}:path==='/api/starters'?{starters:[]}:path==='/api/worlds'?{worlds:[]}:(path.endsWith('/plan')&&!terminalHandles||path==='/api/jobs/planjob')?{id:'planjob',status:'completed',result:{schema:'collection-plan-v1',world_id:'world',plan_job_id:'planjob',config:Object.fromEntries(Object.entries(config).sort(([a],[b])=>a.localeCompare(b))),directions:['<img src=x>'],queries:['exact query']}}:(path.endsWith('/collect')&&!terminalHandles||path==='/api/jobs/collectjob')?{id:'collectjob',status:'completed',result:{status:'completed',captured:0}}:path.endsWith('/plans')?{plans:[{id:'planjob',status:'completed',collection_job_id:null},{id:'consumed',status:'completed',collection_job_id:'collectjob'},{id:'pending',status:'running',collection_job_id:null}]}:path.endsWith('/plan')?{id:'planjob',status:'completed',replayed:true}:path.endsWith('/collect')?{id:'collectjob',status:'completed',replayed:true}:world;return{ok:true,status:200,json:async()=>result};}};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
(async()=>{await vm.runInContext("load('world')",sandbox);document.getElementById('provider').value='openai';document.getElementById('model').value='fixture';document.getElementById('search-provider').value='brave';await document.getElementById('plan').onclick();assert.equal(requests.filter(r=>r.options.method==='POST').length,1);assert.equal(requests.find(r=>r.options.method==='POST').path,'/api/worlds/world/plan');assert.equal(document.getElementById('collect').disabled,false);assert.equal(document.getElementById('plan-review').hidden,false);assert(vm.runInContext('selectedPlan.jobId',sandbox)==='planjob');await document.getElementById('collect').onclick();const collect=requests.find(r=>r.path.endsWith('/collect'));assert.equal(JSON.parse(collect.options.body).plan_job_id,'planjob');assert.equal(document.getElementById('collect').disabled,true);await document.getElementById('plan').onclick();document.getElementById('model').value='changed';document.getElementById('model').oninput();assert.equal(document.getElementById('collect').disabled,true);assert.equal(document.getElementById('plan-review').hidden,true);const before=requests.length;await document.getElementById('collect').onclick();assert.equal(requests.length,before);await vm.runInContext("savedPlans('world')",sandbox);const rows=document.getElementById('saved-plans').children;const restored=rows[1].children.find(n=>n.textContent==='Review saved plan');const postsBefore=requests.filter(r=>r.options.method==='POST').length;await restored.onclick();assert.equal(document.getElementById('model').value,'fixture');assert.equal(document.getElementById('collect').disabled,false);assert.equal(requests.filter(r=>r.options.method==='POST').length,postsBefore);await document.getElementById('collect').onclick();assert.equal(requests.filter(r=>r.path.endsWith('/collect')).length,2);assert(rows[2].children.some(n=>n.textContent==='Inspect collection'));assert(!rows[2].children.some(n=>n.textContent==='Review saved plan'));assert(!rows[3].children.some(n=>n.textContent==='Review saved plan'));config.max_queries=4;await restored.onclick();assert.equal(document.getElementById('collect').disabled,true);assert(document.getElementById('notice').textContent.includes('CLI'));if(terminalHandles){assert(requests.some(r=>r.path==='/api/jobs/planjob'&&!r.options.method));assert(requests.some(r=>r.path==='/api/jobs/collectjob'&&!r.options.method));}})().catch(e=>{console.error(e);process.exitCode=1});'''
    path=tmp_path/'plan-check.cjs';path.write_text(harness)
    result=subprocess.run([node,str(path),str(STATIC/'app.js'),str(terminal_handles).lower()],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
