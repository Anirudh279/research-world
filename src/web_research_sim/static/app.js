'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, availability = {}, workspaceToken = "", selectedPlan = null; 
function notice(message, error=false) { $('notice').textContent=message; $('notice').className=error?'error':'info'; }
async function api(path, body, requestKey) {
  const headers={'Content-Type':'application/json'};if(requestKey)headers['Idempotency-Key']=requestKey;if(workspaceToken)headers.Authorization='Bearer '+workspaceToken;
  const response=await fetch(path, body===undefined?{headers}:{method:'POST',headers,body:JSON.stringify(body)});
  let data; try {data=await response.json();} catch {throw new Error(`Server returned HTTP ${response.status} without JSON.`);}
  if(!response.ok) {const error=new Error(data.error?.message || `HTTP ${response.status}`);error.status=response.status;throw error;}
  return data;
}
function node(tag, text, cls) {const n=document.createElement(tag); if(text!==undefined)n.textContent=String(text); if(cls)n.className=cls; return n;}
function setBusy(value) {busy=value; for(const id of ['plan','freeze','research','compare']) $(id).disabled=value;$('collect').disabled=value||!selectedPlan;}
async function safely(action) {try {await action();} catch(error) {notice(error.message,true);}}
function providerHint() { $('provider-status').textContent=`${$('provider').value}: ${availability[$('provider').value]?'credential present (not verified)':'not configured'} · ${$('search-provider').value}: ${availability[$('search-provider').value]?'credential present (not verified)':'not configured'}`; }
function collectionConfig(){return {...modelArgs(),search_provider:$('search-provider').value,max_queries:3,max_pages:5};}
function configSignature(config){return JSON.stringify(['model_provider','model','search_provider','max_queries','max_pages'].map(key=>config[key]));}
function invalidatePlan(){selectedPlan=null;$('plan-review').replaceChildren();$('plan-review').hidden=true;$('collect').disabled=true;}
function showPlan(saved, jobId, worldId, config) {
  const box=$('plan-review');box.replaceChildren();box.hidden=false;
  if(!Array.isArray(saved?.directions)||!Array.isArray(saved?.queries))throw new Error('The saved plan is missing directions or queries. Collection has not started.');
  if(saved.schema!=='collection-plan-v1'||saved.world_id!==worldId||saved.plan_job_id!==jobId||Object.keys(config).some(key=>saved.config?.[key]!==config[key])||saved.directions.some(item=>typeof item!=='string')||saved.queries.some(item=>typeof item!=='string'))throw new Error('Saved plan binding does not match this request. Collection has not started.');
  selectedPlan={jobId,worldId,config:configSignature(config)};
  box.append(node('h3','Review this saved research plan'),node('small',`Plan job: ${jobId}`));
  box.append(node('h4','Research directions'));const directions=node('ul');for(const item of saved.directions)directions.append(node('li',item));box.append(directions);
  box.append(node('h4','Exact search queries'));const queries=node('ol');for(const item of saved.queries)queries.append(node('li',item));box.append(queries);
  box.append(node('p',`Planning has used one model call. Approval permits up to ${config.max_queries} search calls, one source-selection model call and ${config.max_pages} page captures, within your remaining call limits and hosted spending allocation. No search or capture starts until you choose Approve and collect.`,'hint'));
  $('collect').disabled=busy;
}
async function savedPlans(worldId) {
  const response=await api(`/api/worlds/${encodeURIComponent(worldId)}/plans`);if(current?.id!==worldId)return;
  const box=$('saved-plans');box.replaceChildren();if(!response.plans?.length)return;
  box.append(node('h3','Saved research plans'));
  for(const plan of response.plans){const row=node('div',undefined,'saved-plan');row.append(node('small',`${plan.id} · ${plan.status}`));
    if(plan.collection_job_id){row.append(node('p',`Already approved · collection job ${plan.collection_job_id}`,'hint'));const inspect=node('button','Inspect collection','secondary');inspect.onclick=()=>safely(async()=>{const result=await api(`/api/jobs/${encodeURIComponent(plan.collection_job_id)}`);if(current?.id!==worldId)return;row.append(node('pre',JSON.stringify(result,null,2)));});row.append(inspect);}
    else if(plan.status==='completed'){const review=node('button','Review saved plan','secondary');review.onclick=()=>safely(async()=>{if(busy)return;invalidatePlan();const result=await api(`/api/jobs/${encodeURIComponent(plan.id)}`);if(current?.id!==worldId)return;const config=result.result?.config;if(result.status!=='completed'||!config)throw new Error('This plan is not available for review.');if(config.max_queries!==3||config.max_pages!==5)throw new Error('This plan uses custom limits. Review and approve it through the CLI with its exact saved settings.');invalidatePlan();$('provider').value=config.model_provider;$('model').value=config.model;$('search-provider').value=config.search_provider;providerHint();showPlan(result.result,result.id,worldId,config);notice('Saved plan restored for review. Collection has not started.');});row.append(review);}
    box.append(row);
  }
}
function showStorage(storage) {
  if(!storage || !['local','s3'].includes(storage.backend)){$('storage-status').textContent='Archive storage information unavailable. No connectivity check has been established.';return;}
  if(storage.status!=='configured_unverified'){$('storage-status').textContent='Archive storage configuration needs attention. Run the CLI preflight check for this workspace before using it. Connectivity is not verified by this display.';return;}
  const backend=storage.backend==='s3'?'S3-compatible object storage':'Local file storage';
  const metadata=storage.metadata==='local_sqlite'?'Workspace metadata: local SQLite.':'Workspace metadata location: unknown.';
  $('storage-status').textContent=`${backend} configured. ${metadata} Connectivity is not verified by this display.`;
}
function showCosts(costs) {
  if(!costs){$('cost-status').textContent='Cost information unavailable.';return;}
  const dollars=value=>value===null||value===undefined?'not allocated':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:6}).format(value/1000000);
  $('cost-status').textContent=`Allocation: ${dollars(costs.allocation)} · Reserved or settled: ${dollars(costs.charged)} · Remaining: ${dollars(costs.remaining)} · Unknown charges: ${costs.actual_cost_unknown_operations}. Pricing policy: ${costs.policy_file_present?'present; checked per call':'missing; hosted calls will stop'}.${costs.admission_blocked?' Admission blocked: configure allocation or inspect a breached cost bound.':''}`;
}
async function refreshCosts(){showCosts(await api('/api/costs'));}
async function worlds() {
  const data=await api('/api/worlds'); $('world-list').replaceChildren();
  for(const world of data.worlds) {const b=node('button',world.question,'world-link');b.title=world.question;b.onclick=()=>safely(()=>load(world.id));$('world-list').append(b);}
}
async function starters() {
  const data=await api('/api/starters'); $('starter-list').replaceChildren();
  for(const starter of data.starters) {
    const card=node('article',undefined,'starter');card.append(node('h3',starter.title),node('p',starter.question));
    const button=node('button','Install starter','secondary');button.disabled=busy;
    button.onclick=()=>safely(async()=>{
      if(busy)return;setBusy(true);button.disabled=true;
      try {const installed=await api(`/api/starters/${encodeURIComponent(starter.slug)}`,{});await worlds();await load(installed.world_id);$('snapshot').value=installed.default_snapshot_id;notice('Synthetic starter installed. No LLM has run. Choose a model below, then select Run research agent to reason over the January 2 snapshot. Provider charges may apply.');}
      catch(error){notice(`${error.message} Installation may have completed. Refresh Worlds and check for the starter before trying again.`,true);}
      finally{setBusy(false);button.disabled=false;}
    });card.append(button);$('starter-list').append(card);
  }
}
function showCaptureDiagnostics(result, captures=[]) {
  const box=$('capture-diagnostics');box.replaceChildren();box.hidden=true;
  const failures=Array.isArray(result?.failures)?result.failures:[];
  const gaps=Array.isArray(result?.extraction_gaps)?result.extraction_gaps:[];
  if(!failures.length&&!gaps.length)return;
  box.hidden=false;box.append(node('h3','Latest collection gaps'));
  box.append(node('p',`${result.captured ?? 'Unknown'} captures saved · ${failures.length} source failures · ${gaps.length} extraction gaps. Saved does not mean complete or usable.`,'hint'));
  const list=node('ul');
  for(const failure of failures)list.append(node('li',`${failure.url || 'Source URL unavailable'} — ${failure.error_code || failure.error_type || 'capture_failed'}: ${failure.message || 'No usable capture was saved. Inspect the source before retrying.'}`));
  for(const gap of gaps){const capture=captures.find(c=>c.id===gap.document_id);list.append(node('li',`${capture?.url || gap.document_id || 'Unknown source'} — ${gap.error || 'Extraction incomplete; inspect the saved original.'}`));}
  box.append(list);
}
function showSavedFailures(events=[]) {
  const box=$('capture-failures');box.replaceChildren();
  const failed=events.filter(event=>event.kind==='capture_failed'||event.type==='capture_failed');box.hidden=!failed.length;
  if(!failed.length)return;box.append(node('h3','Recorded source failures'));
  const list=node('ul');for(const event of failed){const failure=event.data||{};list.append(node('li',`${failure.url || 'Unknown source'} — ${failure.error_code || failure.error_type || 'capture_failed'}: ${failure.message || 'Capture did not complete.'}`));}box.append(list);
}
async function load(id) {
  await refreshCosts();
  const data=await api(`/api/worlds/${encodeURIComponent(id)}`); if(current?.id!==id){invalidatePlan();$('capture-diagnostics').replaceChildren();$('capture-diagnostics').hidden=true;} current=data.world;showSavedFailures(data.events||[]);await savedPlans(id);if(current?.id!==id)return;
  $('workspace').hidden=false; $('research-section').hidden=false;
  $('world-title').textContent=current.question; $('world-id').textContent=current.id;
  $('usage').replaceChildren(); for(const [kind, value] of Object.entries(data.usage||{})) {const n=node('div',undefined,'metric');n.append(node('strong',`${value.reserved} / ${value.limit}`),node('span',kind.replaceAll('_',' ')));$('usage').append(n);}
  $('captures').replaceChildren(); $('capture-empty').hidden=data.captures.length>0;
  for(const capture of data.captures) {const row=node('tr');const source=node('td'); const title=node('div',capture.title||capture.url);const url=node('small',capture.url);source.append(title,url);const extraction=node('td',capture.extraction_status || 'Unknown / legacy');if(capture.extraction_error)extraction.append(node('small',capture.extraction_error));row.append(source,node('td',capture.status),extraction,node('td',capture.captured_at?new Date(capture.captured_at).toLocaleString():'—'));$('captures').append(row);}
  const old=$('snapshot').value;$('snapshot').replaceChildren();for(const snap of data.snapshots) {const option=node('option',`${snap.count} pages · ${new Date(snap.cutoff).toLocaleString()} · ${snap.id}`);option.value=snap.id;$('snapshot').append(option);}if([...$('snapshot').options].some(o=>o.value===old))$('snapshot').value=old;
  $('runs').replaceChildren();if(!data.runs.length)$('runs').append(node('p','No agent runs yet. Freeze captured evidence, then run a research agent.','empty'));
  for(const run of data.runs) {const card=node('article',undefined,'run');const label=node('label');const box=node('input');box.type='checkbox';box.value=run.id;box.name='run';label.append(box,document.createTextNode(`${run.model} · ${run.status} · ${run.id}`));card.append(label,node('small',`Snapshot: ${run.snapshot_id}`),node('p',run.answer||'No answer available.'));const details=node('button','View full record','secondary');details.onclick=()=>safely(async()=>{const record=await api(`/api/runs/${encodeURIComponent(run.id)}`);let pre=card.querySelector('pre');if(!pre){pre=node('pre');card.append(pre);}pre.textContent=JSON.stringify(record,null,2);});card.append(details);const checks=node('button','View evidence checks','secondary');checks.onclick=()=>safely(async()=>{const result=await api(`/api/runs/${encodeURIComponent(run.id)}/evaluation`);let panel=card.querySelector('.evidence-checks');if(!panel){panel=node('section',undefined,'evidence-checks');card.append(panel);}panel.replaceChildren(node('p','Deterministic evidence checks only. These do not establish semantic support or factual correctness.','hint'),node('pre',JSON.stringify(result,null,2)));});card.append(checks);$('runs').append(card);}
}
async function job(path, body, purpose="research") {
  const submittedWorld=current?.id;
  if(busy)return;setBusy(true);let submitted=false;
  const storageKey='pending-job:'+JSON.stringify([path,body]);
  try {let requestKey=sessionStorage.getItem(storageKey);if(!requestKey){requestKey=crypto.randomUUID();sessionStorage.setItem(storageKey,requestKey);}
    let result=await api(path,body,requestKey);result=result.job||result;submitted=true;
    $('job-status').textContent=`Job ${result.id}: ${result.status}`;
    while(['queued','pending','running'].includes(result.status)) {await new Promise(r=>setTimeout(r,2000));result=await api(`/api/jobs/${encodeURIComponent(result.id)}`);$('job-status').textContent=`Job ${result.id}: ${result.status}`;if(current)await load(current.id);}
    // Submission responses may be terminal handles without the saved result.
    // Recover through GET only, never repeat a paid submission to read output.
    if(!Object.prototype.hasOwnProperty.call(result,'result'))result=await api(`/api/jobs/${encodeURIComponent(result.id)}`);
    sessionStorage.removeItem(storageKey);
    if(['failed','error','cancelled','interrupted'].includes(result.status))throw new Error(`Job ${result.id}: ${typeof result.error==='string'?result.error:JSON.stringify(result.error||result.status)}`);
    if(purpose==='plan'){await load(current.id);if(current.id!==submittedWorld||configSignature(collectionConfig())!==configSignature(body)){invalidatePlan();notice('Plan saved, but the selected world or settings changed. Create a plan for the current settings.',true);return;}showPlan(result.result,result.id,submittedWorld,body);notice('Plan ready for review. Collection has not started.');return;}
    if(purpose==='collect')invalidatePlan();
    const outcome=result.result?.status; notice(outcome&&outcome!=='completed'?`Job finished with result: ${outcome}. Inspect the saved record; this is not a verified research success.`:'Operation completed. Inspect the saved evidence and result below.');await load(current.id);showCaptureDiagnostics(result.result);
  } catch(error) {notice(`${error.message}${sessionStorage.getItem(storageKey)?' Submission state is uncertain. Retry with unchanged settings in this tab to inspect the same job without resubmitting.':submitted?' Inspect the saved job before starting another.':''}`,true);}finally{setBusy(false);}
}
$('create-form').onsubmit=event=>{event.preventDefault();safely(async()=>{const result=await api('/api/worlds',{question:$('question').value,budgets:{model_calls:Number($('model-budget').value),search_calls:Number($('search-budget').value),capture_calls:Number($('capture-budget').value)}});await worlds();await load((result.world||result).id);notice('World created. Configure a model and collect evidence.');});};
function modelArgs(){const model=$('model').value.trim();if(!model)throw new Error('Enter the model ID you want to use.');return {model_provider:$('provider').value,model};}
$('plan').onclick=()=>safely(()=>{const config=collectionConfig();invalidatePlan();return job(`/api/worlds/${encodeURIComponent(current.id)}/plan`,config,'plan');});
$('collect').onclick=()=>safely(()=>{const config=collectionConfig();if(!selectedPlan||selectedPlan.worldId!==current.id||selectedPlan.config!==configSignature(config)){invalidatePlan();throw new Error('Create and review a plan for these settings before collecting.');}return job(`/api/worlds/${encodeURIComponent(current.id)}/collect`,{...config,plan_job_id:selectedPlan.jobId},'collect');});
$('freeze').onclick=()=>safely(async()=>{if(busy)return;setBusy(true);try{const snapshot=await api(`/api/worlds/${encodeURIComponent(current.id)}/freeze`,{});await load(current.id);$('snapshot').value=snapshot.id;notice('Snapshot frozen. Research runs will use this fixed evidence.');}finally{setBusy(false);}});
$('research').onclick=()=>safely(()=>{if(!$('snapshot').value)throw new Error('Freeze a snapshot first.');return job(`/api/snapshots/${encodeURIComponent($('snapshot').value)}/research`,{...modelArgs(),max_steps:8});});
$('compare').onclick=()=>safely(async()=>{const ids=[...document.querySelectorAll('input[name="run"]:checked')].map(n=>n.value);if(ids.length<2)throw new Error('Select at least two runs.');const result=await api('/api/compare',{run_ids:ids});$('comparison').hidden=false;$('comparison').textContent=JSON.stringify(result,null,2);});
$('refresh').onclick=()=>safely(async()=>{await worlds();if(current)await load(current.id);});$('provider').onchange=()=>{providerHint();invalidatePlan();};$('search-provider').onchange=()=>{providerHint();invalidatePlan();};$('model').oninput=invalidatePlan;
async function connect(token="", initial=false) {
  if(busy){notice('Wait for the current job observation to finish before changing connections.',true);return;}
  workspaceToken=token;
  try {const status=await api('/api/status');availability=status.providers||{};showCosts(status.hosted_costs);showStorage(status.storage);$('connection').textContent='● Connected';$('version').textContent=status.version;providerHint();await worlds();await starters();$('disconnect').hidden=false;notice(status.limitations?.length?status.limitations.join(' '):'Workspace connected. Choose your research path.');}
  catch(error){workspaceToken="";$('connection').textContent='○ Not connected';$('disconnect').hidden=true;notice(error.status===401?'This workspace needs its access token. Enter it in the connection form to connect.':error.message,!initial||error.status!==401);}
}
$('connect-form').onsubmit=event=>{event.preventDefault();const token=$('workspace-token').value.trim();$('workspace-token').value='';connect(token);};
$('disconnect').onclick=()=>{if(busy){notice('Wait for the current job observation to finish before disconnecting.',true);return;}workspaceToken='';availability={};current=null;invalidatePlan();$('saved-plans').replaceChildren();$('cost-status').textContent='Connect to inspect the allocation.';$('storage-status').textContent='Connect to inspect archive storage.';$('workspace-token').value='';$('connection').textContent='○ Not connected';$('disconnect').hidden=true;$('world-list').replaceChildren();$('starter-list').replaceChildren(node('p','Connect to choose a starter world.','empty'));$('workspace').hidden=true;$('research-section').hidden=true;$('captures').replaceChildren();$('runs').replaceChildren();$('comparison').textContent='';notice('Disconnected. The access token has been cleared from this tab.');};
connect('',true);
