'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const token=$('meta[name="rowdy-token"]').content;
const state={boot:null,p:null,m:null,source:'',sourceHash:'',context:'',result:null,tab:'data',trace:null,event:null,
    live:false,request:0,session:crypto.randomUUID(),epoch:0,revision:0,timer:null,saveTimer:null,stage:'result',expression:null,
    clock:'event_time',tableFilter:'',busy:false,baseline:null};
async function api(path,body){
    const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-Rowdy-Token':token,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const value=await r.json(); if(!r.ok)throw new Error(value.error||'Operation failed'); return value;
}
function q(path,obj){return path+'?'+new URLSearchParams(obj).toString()}
let toastTimer;
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4800)}
function dialog(title,body){state.returnFocus=document.activeElement;$('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;$('#dialog').showModal()}
function closeDialog(){$('#dialog').close();state.returnFocus?.focus?.()}
function status(text,kind='idle'){$('#run-status').innerHTML='<span class="status-dot '+kind+'"></span><span>'+esc(text)+'</span>'+(state.result?'<button class="receipt-link" data-action="receipt">Receipt ↗</button>':'')}
function value(v){return v===null?'<span class="null">NULL</span>':v===undefined?'<span class="null">not retained</span>':esc(typeof v==='object'?JSON.stringify(v):v)}
function pretty(v){return esc(JSON.stringify(v,null,2))}
function badge(text,kind=''){return '<span class="tag '+kind+'">'+esc(text)+'</span>'}
function names(){return state.p.models.map(m=>m.name)}

async function bootstrap(preferred){
    const boot=await api('/api/bootstrap');state.boot=boot;$('#version').textContent='row(dy) '+boot.version;
    const id=preferred||localStorage.getItem('rowdy.project');
    await project(boot.projects.find(p=>p.id===id)||boot.projects[0]);
}
async function project(p){
    pause(false);state.epoch++;state.p=p;state.trace=null;state.event=null;state.result=null;state.baseline=null;state.tab='data';
    localStorage.setItem('rowdy.project',p.id);$('#project-title').textContent=p.title;$('#project-folder').textContent=p.id;
    $('#snapshot').textContent='snapshot '+p.snapshot_hash.slice(0,8);$('#branch').textContent='⑂ '+p.git.branch;
    $('#file-tree').innerHTML='<div class="folder">⌄ &nbsp;models</div>'+p.models.map(m=>'<button class="file" data-model="'+esc(m.name)+'"><span class="sql-small">SQL</span><span class="file-label">'+esc(m.name)+'</span></button>').join('');
    await openModel(p.default_model);updateHistory();
}
async function openModel(name,keepTrace=false){
    pause(false);const epoch=++state.epoch;state.stage='result';state.expression=null;state.baseline=null;
    const info=await api(q('/api/model',{project:state.p.id,model:name}));if(epoch!==state.epoch)return;
    state.m=info.model;state.source=info.sql;state.sourceHash=info.source_hash;state.context=info.context_hash;
    state.result=null;state.busy=false;state.revision=0;state.tableFilter='';state.tab=keepTrace?'trace':'data';
    if(!keepTrace){state.trace=null;state.event=null}
    const draft=info.draft;
    $('#editor').value=draft?.context_hash===state.context?draft.sql:info.sql;
    $('#file-name').textContent=name+'.sql';$('#file-path').textContent=info.model.file;$('#model-title').textContent=name;
    $('#grain').innerHTML='<span>Grain </span> <b>'+esc(info.model.grain||'Not declared')+'</b>';
    $('#branch').textContent='⑂ '+info.git.branch;
    $$('.file').forEach(b=>b.classList.toggle('active',b.dataset.model===name));
    paintCode();render();$('#evidence-content').scrollTop=0;status(draft&&draft.sql!==info.sql?'Restored scratch · paused. Repository file is unchanged.':'Ready · fixed inputs. Run to inspect this model.');
    const saved=JSON.parse(localStorage.getItem('rowdy.caret.'+state.p.id+'.'+name)||'null');
    if(saved)$('#editor').setSelectionRange(Math.min(saved[0],$('#editor').value.length),Math.min(saved[1],$('#editor').value.length));
    $('#editor').focus();caret();
    if(draft&&draft.context_hash!==state.context)toast('An older scratch exists. The project changed; current source was opened instead.');
}
function paintCode(){
    const sql=$('#editor').value;
    const pattern=/(--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|\b(?:SELECT|FROM|WHERE|WITH|AS|ORDER|BY|GROUP|HAVING|JOIN|LEFT|RIGHT|INNER|ON|IN|IS|NOT|NULL|AND|OR|CASE|WHEN|THEN|ELSE|END|OVER|PARTITION|DESC|ASC|DISTINCT|LIMIT|UNION|ALL|COUNT|SUM|MAX|MIN|ROW_NUMBER)\b|\b\d+(?:\.\d+)?\b)/gi;
    let out='',pos=0;for(const m of sql.matchAll(pattern)){out+=esc(sql.slice(pos,m.index));const t=m[0],cls=t.startsWith('--')||t.startsWith('/*')?'comment':t[0]==="'"?'str':/^\d/.test(t)?'num':'kw';out+='<span class="'+cls+'">'+esc(t)+'</span>';pos=m.index+t.length}
    $('#highlight').innerHTML=out+esc(sql.slice(pos))+'\n';$('#line-numbers').textContent=Array.from({length:sql.split('\n').length},(_,i)=>i+1).join('\n');
    const dirty=sql!==state.source;$('#dirty-dot').hidden=!dirty;$('#save-state').textContent=dirty?'Scratch · not on disk':'Saved on disk';
    syncScroll();
}
function syncScroll(){$('#highlight').scrollTop=$('#editor').scrollTop;$('#highlight').scrollLeft=$('#editor').scrollLeft;$('#line-numbers').scrollTop=$('#editor').scrollTop}
function caret(){const e=$('#editor'),before=e.value.slice(0,e.selectionStart),lines=before.split('\n');$('#caret').textContent='Ln '+lines.length+', Col '+(lines.at(-1).length+1);$('#watch-selection').hidden=e.selectionStart===e.selectionEnd; if(state.m)localStorage.setItem('rowdy.caret.'+state.p.id+'.'+state.m.name,JSON.stringify([e.selectionStart,e.selectionEnd]));}
function changed(){
    state.revision++;paintCode();caret();clearTimeout(state.timer);clearTimeout(state.saveTimer);
    if(state.result)status('Edited · showing the last valid result from earlier SQL.','stale');
    const captured={project:state.p.id,model:state.m.name,sql:$('#editor').value,context_hash:state.context,source_hash:state.sourceHash};
    state.saveTimer=setTimeout(()=>api('/api/draft',captured).catch(e=>toast(e.message)),180);
    if(state.live)state.timer=setTimeout(()=>run(false),260);
}
function pause(notify=true){
    clearTimeout(state.timer);state.live=false;$('#live').classList.remove('on');$('#live').setAttribute('aria-pressed','false');
    if(state.m){const request=++state.request;api('/api/cancel',{project:state.p.id,model:state.m.name,session:state.session,request}).catch(()=>{})}
    if(notify)status('Paused · edits are saved as scratch, without execution.','idle');
}
async function run(verify=false){
    clearTimeout(state.timer);
    const epoch=state.epoch,revision=state.revision,request=++state.request,sql=$('#editor').value;
    state.busy=true;status(verify?'Verifying independent expectations…':'Evaluating fixed inputs…');
    try{
        const result=await api('/api/run',{project:state.p.id,model:state.m.name,sql,context_hash:state.context,
            request,session:state.session,stage:state.stage,expression:state.expression,verify});
        if(epoch!==state.epoch||revision!==state.revision||request!==state.request)return;
        state.result=result;state.busy=false;
        if(verify)state.tab='checks';
        status(verify?(result.status==='passed'?'Local checks passed · warehouse and consumer not verified.':'Local verification is not passed. Inspect the evidence below.'):
            result.preview.count+' rows · '+result.execution_ms+' ms · '+(state.live?'live on fixed inputs':'local snapshot'),verify&&result.status!=='passed'?'stale':'');
        render();updateHistory();
    }catch(e){
        if(epoch!==state.epoch||revision!==state.revision||request!==state.request)return;
        state.busy=false;status('Not evaluated · '+e.message+(state.result?' · last valid result retained.':''),'stale');
        if(!state.result)$('#evidence-content').innerHTML='<div class="error-box">'+esc(e.message)+'</div>';
    }
}
function setTab(tab){state.tab=tab;render();$('#evidence-content').scrollTop=0}
function grid(result,{traceable=false,numbered=true}={}){
    if(!result?.rows?.length)return '<div class="table-note">No matching rows in this result scope.</div>';
    const columns=result.columns||Object.keys(result.rows[0]);let rows=result.rows;
    if(state.tableFilter)rows=rows.filter(r=>JSON.stringify(r).toLowerCase().includes(state.tableFilter.toLowerCase()));
    return '<div class="grid-wrap"><table><thead><tr>'+(numbered?'<th class="row-num">#</th>':'')+columns.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr></thead><tbody>'+rows.map((r,i)=>'<tr>'+(numbered?'<td class="row-num">'+(i+1)+'</td>':'')+columns.map(c=>'<td>'+((traceable&&state.p.trace?.identities.includes(c)&&r[c]!=null)?'<button class="id-link" data-trace-field="'+esc(c)+'" data-trace-row="'+result.rows.indexOf(r)+'">'+value(r[c])+'</button>':value(r[c]))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}
function watchStrip(){
    if(!state.event||!state.result)return '';
    const cfg=state.p.trace;if(!cfg)return '';
    const event=state.event,rows=state.result.output.rows;
    const hasKeys=state.result.output.columns.includes(cfg.event_key)&&state.result.output.columns.includes(cfg.namespace);
    const found=hasKeys?rows.filter(r=>String(r[cfg.event_key])===event.id&&r[cfg.namespace]===state.trace.root.namespace):[];
    const outcome=!hasKeys?'Not projected':found.length?'Retained in output':'Absent from output';
    let html='<div class="watch-strip"><div class="watch-top"><span>WATCHING THIS RECORD</span><button data-action="clear-watch" title="Stop watching">×</button></div><div class="watch-name">'+esc(event.id)+' '+badge(outcome,found.length?'good':'warn')+'</div><div class="watch-sub">'+esc(event.name)+' · '+esc(state.trace.root.namespace)+'</div>';
    for(const d of state.result.downstream||[]){if(d.error)continue;const b=d.before[0],a=d.after[0];if(!b||!a)continue;const fields=Object.keys(a).filter(k=>typeof a[k]==='number');for(const k of fields)html+='<div class="watch-sub">'+esc(d.title)+' &nbsp; <b>'+esc(b[k])+' → <span style="color:var(--accent)">'+esc(a[k])+'</span></b></div>'}
    return html+'</div>';
}
function render(){
    $$('.tabs button').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===state.tab)));
    const panel=$('#evidence-content'),scroll=panel.scrollTop;
    if(state.tab==='trace'){renderTrace();return}
    const r=state.result;
    if(!r){panel.innerHTML='<div class="empty"><span class="empty-mark">↳</span><h2>Code. Meet consequence.</h2><p>Run this model, follow an ID,<br>then see what changes as you edit.</p><button class="button" data-action="run">Run the model <kbd>⌘ ↵</kbd></button></div>';return}
    if(state.tab==='changes'){
        const d=state.baseline?clientDiff(state.baseline.output,r.output,state.m.keys):r.difference;
        panel.innerHTML='<div class="diff-head"><div class="eyebrow">CONTROLLED BEFORE / AFTER</div><h2>What did your edit change?</h2><p>Same fixed inputs. '+(state.baseline?'Pinned execution '+esc(state.baseline.id.slice(0,8)):'Compared with the source file on disk')+'.<br>This is local replay, not warehouse equivalence.</p>'+(d.available?'<div class="diff-counts"><span class="add">+'+d.added+' added</span><span class="remove">−'+d.removed+' removed</span><span>'+d.changed+' changed</span></div>':'<p>'+esc(d.reason)+'</p>')+'</div>'+(d.available?d.changes.map((c,i)=>'<button class="change-row" data-change="'+i+'"><span class="tag '+(c.kind==='added'?'good':'warn')+'">'+c.kind+'</span><code>'+c.key.map(esc).join(' / ')+'</code><span>Inspect ↗</span></button>').join(''):'')+(d.available&&!d.changes.length?'<div class="empty" style="height:220px"><span class="empty-mark">=</span><p>No observed differences on these inputs.</p></div>':'');
        state.displayDiff=d;
    }else if(state.tab==='checks'){
        const tests=r.checks||[],passed=tests.filter(c=>c.status==='passed').length;
        panel.innerHTML='<div class="checks-summary"><div class="eyebrow">INDEPENDENT EXPECTATIONS</div><h2>'+passed+' of '+tests.length+' checks passed</h2><p>'+(r.kind==='verification'?'Explicit local verification · '+esc(r.id.slice(0,8)):'Continuous feedback only. Choose Verify for an explicit checkpoint.')+'<br>Expectations come from the registered profile, not the candidate output.</p></div>'+tests.map(c=>'<div class="check"><span class="check-icon '+c.status+'">'+(c.status==='passed'?'✓':'×')+'</span><div><strong>'+esc(c.title)+'</strong><small>'+esc(c.status==='passed'?'Expected behavior observed on the fixed snapshot':c.message||'Observed output differs from the independent expectation')+'</small><details><summary>Inspect evidence</summary><pre>'+esc(c.sql||'')+'\n\nExpected: '+pretty(c.expected)+'\nObserved: '+pretty(c.actual)+'</pre></details></div>'+badge(c.status,c.status==='passed'?'good':'bad')+'</div>').join('')+'<div class="table-note">Warehouse execution, deployment, and consumer-visible behavior are separate claims. None is established by these local checks.</div>';
    }else{
        panel.innerHTML=watchStrip()+'<div class="data-tools"><span>'+r.preview.count+' rows</span><select id="preview-stage" aria-label="Preview scope"><option value="result">Final result</option>'+r.stages.map(c=>'<option value="'+esc(c.name)+'" '+(state.stage===c.name?'selected':'')+'>'+esc(c.name)+'</option>').join('')+'</select><button class="text-action" data-action="pin">'+(state.baseline?'Pinned baseline':'Pin baseline')+'</button><input id="filter" type="search" aria-label="Filter fetched rows" placeholder="Filter fetched rows" value="'+esc(state.tableFilter)+'"></div>'+
            (r.expression?.available?'<div class="watch-strip"><div class="trace-kicker">EXPRESSION · '+esc(r.expression.scope)+' INPUTS</div><div class="watch-name">'+esc(r.expression.expression)+'</div>'+grid({columns:r.expression.columns,rows:r.expression.rows.filter(x=>!state.event||(String(x[state.p.trace.event_key])===state.event.id&&x[state.p.trace.namespace]===state.trace.root.namespace))},{numbered:false})+'</div>':r.expression?'<div class="warning-line">'+esc(r.expression.reason)+'</div>':'')+
            grid(r.preview,{traceable:true})+'<div class="table-note">'+esc(r.scope)+'<br>Code '+esc(r.sql_hash.slice(0,8))+' · input '+esc(r.identity.input_hash.slice(0,8))+' · '+esc(r.engine)+'</div>';
    }
    panel.scrollTop=scroll;
}
function clientDiff(a,b,keys){
    function index(x){const out=new Map();for(const r of x.rows){if(!keys.every(k=>r[k]!=null))throw Error('Keys missing or NULL');const k=JSON.stringify(keys.map(c=>r[c]));if(out.has(k))throw Error('Keys are not unique');out.set(k,r)}return out}
    try{const x=index(a),y=index(b),changes=[];for(const k of new Set([...x.keys(),...y.keys()])){if(JSON.stringify(x.get(k))===JSON.stringify(y.get(k)))continue;changes.push({key:JSON.parse(k),kind:!x.has(k)?'added':!y.has(k)?'removed':'changed',before:x.get(k),after:y.get(k)})}return{available:true,changes,...Object.fromEntries(['added','removed','changed'].map(k=>[k,changes.filter(c=>c.kind===k).length]))}}
    catch(e){return{available:false,reason:e.message}}
}
async function traceValue(type,v,ns,origin){
    pause(false);const epoch=state.epoch;status('Following registered identities and records…');
    try{const t=await api('/api/trace',{project:state.p.id,context_hash:state.context,type,value:String(v),namespace:ns,
        start:state.p.trace.window[0],end:state.p.trace.window[1],origin_run:origin||null});
        if(epoch!==state.epoch)return;state.trace=t;state.event=null;state.tab='trace';render();$('#evidence-content').scrollTop=0;status(t.events.length+' events · fixed observation · '+t.unresolved.length+' identity exceptions');updateHistory();
    }catch(e){toast(e.message);status(e.message,'stale')}
}
function renderTrace(){
    const panel=$('#evidence-content'),t=state.trace;
    if(!t){panel.innerHTML='<div class="empty"><span class="empty-mark">⌖</span><h2>Every record has a story.</h2><p>Click an ID in a query result,<br>or start from a registered identifier.</p><button class="button" data-action="trace-dialog">Trace an ID</button></div>';return}
    const events=[...t.events].sort((a,b)=>a[state.clock].localeCompare(b[state.clock])||a.id.localeCompare(b.id));
    panel.innerHTML='<div class="trace-head"><div class="trace-kicker">RECORD TRACE · '+esc(t.root.type)+'</div><div class="trace-root">'+esc(t.root.value)+'<span>'+esc(t.root.namespace)+'</span></div><div class="trace-meta">'+esc(t.start.slice(0,10))+' · '+esc(t.start.slice(11,16))+'–'+esc(t.end.slice(11,16))+' UTC<br>'+events.length+' distinct events · observed from registered records</div></div><div class="trace-controls"><select id="trace-clock" aria-label="Timeline clock"><option value="event_time" '+(state.clock==='event_time'?'selected':'')+'>Event time</option><option value="arrival_time" '+(state.clock==='arrival_time'?'selected':'')+'>Arrival time</option></select><span>'+t.queries.length+' bounded lookups</span><button class="text-action" data-action="coverage">Coverage ↗</button></div><div class="timeline">'+events.map(e=>{const missing=e.stages.some(s=>s.state==='not_found_in_scope'&&s.kind!=='related_aggregate');return'<button class="event '+(missing?'missing ':'')+(state.event?.id===e.id?'selected':'')+'" data-event="'+esc(e.id)+'"><time>'+esc(e[state.clock].slice(11,19))+'</time><span class="event-dot"></span><div><strong>'+esc(e.name)+'</strong><small>'+esc(e.id)+' · '+esc(e.record.session_id||e.record.order_id||'')+(e.attempts.length>1?' · '+e.attempts.length+' deliveries':'')+'</small></div>'+(missing?badge('Pipeline gap','warn'):e.reason.kind==='declared_mapping'?badge('Linked identity'):badge('Observed'))+'</button>'}).join('')+'</div>'+(t.unresolved.length?'<button class="warning-line" style="width:100%;text-align:left" data-action="identities">△ '+t.unresolved.length+' ambiguous record excluded · inspect identity evidence ↗</button>':'')+
        (state.event?eventDetail(state.event):'<div class="table-note">Select an event to inspect its representations across tables. A missing match is not yet an explanation.</div>');
}
function eventDetail(e){
    const missing=e.stages.find(s=>s.state==='not_found_in_scope'&&s.model&&s.kind!=='related_aggregate');
    return '<div class="event-detail"><div class="detail-head"><strong>'+esc(e.id)+' · through the pipeline</strong><button class="text-action" data-action="event-record">Inspect records ↗</button></div><div class="path">'+e.stages.map((s,i)=>'<button class="path-node '+(s.state==='found'?'':'absent')+'" data-stage-record="'+i+'"><strong>'+esc(s.name)+'</strong><small>'+esc(s.kind==='related_aggregate'?'Related aggregate':s.state==='found'?s.rows.length+' representation'+(s.rows.length===1?'':'s'):'Not found')+'</small></button>').join('')+'</div><p class="detail-note">'+(missing?'The event exists upstream but is absent from '+esc(missing.table)+'. Inspect the transformation before assigning a cause.':'These are observed key matches. They do not prove physical execution lineage.')+'</p>'+(missing?'<button class="button detail-action" data-edit-model="'+esc(missing.model)+'">Open transformation <span>↗</span></button>':'')+'<p class="detail-note">'+esc(e.reason.detail)+'<br>Event '+esc(e.event_time.slice(11,19))+' · arrival '+esc(e.arrival_time.slice(11,19))+' UTC</p></div>';
}
function traceDialog(){
    const cfg=state.p.trace;if(!cfg){toast('No trace profile registered');return}
    dialog('Start with an identifier','<p>Search the registered namespace and time window. No cross-system identity is guessed.</p><label>Identifier type</label><select id="trace-type">'+cfg.identities.map(i=>'<option>'+esc(i)+'</option>').join('')+'</select><label>Value</label><input id="trace-value" placeholder="e.g. U-1042" autocomplete="off"><label>Namespace</label><input id="trace-namespace" value="'+esc(cfg.default_namespace)+'"><p>'+esc(cfg.window.join(' → '))+'</p><div class="dialog-actions"><button class="primary" id="trace-submit">Trace records ↗</button></div>');
    $('#trace-submit').onclick=()=>{const type=$('#trace-type').value,v=$('#trace-value').value,ns=$('#trace-namespace').value;closeDialog();traceValue(type,v,ns)};$('#trace-value').focus();
}
async function updateHistory(){try{const h=await api(q('/api/history',{project:state.p.id}));$('#history-count').textContent=h.length||''}catch{}}
async function history(){
    const rows=await api(q('/api/history',{project:state.p.id}));
    dialog('Saved evidence','<p>Historical observations keep their original code and input identity. Reopening does not rerun anything.</p>'+rows.map(r=>'<button class="option" data-history="'+r.id+'"><div><strong>'+esc(r.model||'Record trace')+'</strong><small>'+esc(r.kind)+' · '+esc(r.created_at.slice(11,19))+' UTC · '+esc(r.id.slice(0,8))+'</small></div>'+badge(r.status||'observed')+'</button>').join(''));
}
async function reviewFile(){
    const sql=$('#editor').value;
    if(sql===state.source){toast('The buffer matches the source file on disk.');return}
    pause(false);
    const capture={project:state.p.id,model:state.m.name,sql,context_hash:state.context,source_hash:state.sourceHash};
    dialog('Review the actual source change','<p>This writes only <code>'+esc(state.m.file)+'</code> in the registered project. It does not commit, push, run dbt, or deploy. A source or semantic-context conflict blocks the write.</p><div class="diff-code"><div><h3>ON DISK</h3><pre>'+esc(state.source)+'</pre></div><div><h3>YOUR BUFFER</h3><pre>'+esc(sql)+'</pre></div></div><p>'+((state.result?.kind==='verification'&&state.result.sql===sql)?'Local verification: '+esc(state.result.status)+'. Warehouse and consumer verification remain separate.':'This buffer has not completed explicit local verification. Saving is not a verification claim.')+'</p><div class="dialog-actions"><button class="button" id="cancel-apply">Keep editing</button><button class="primary" id="apply-file" '+(!state.p.writable?'disabled':'')+'>Apply to source file ↗</button></div>');
    $('#cancel-apply').onclick=closeDialog;
    $('#apply-file').onclick=async()=>{try{const result=await api('/api/apply',capture);state.source=result.sql;state.sourceHash=result.source_hash;state.context=result.context_hash;state.p.context_hash=result.context_hash;
        const prior={...capture,sql:result.previous_sql,context_hash:result.context_hash,source_hash:result.source_hash};
        closeDialog();paintCode();status('Source file updated · earlier results remain historical. No commit or deployment.','stale');
        dialog('Source updated · real Git diff','<p>The explicit file write completed. Unrelated files were not touched.</p><pre>'+esc(result.git_diff||'No Git diff is available in this project.')+'</pre><div class="dialog-actions"><button class="button" id="undo-apply">Undo this edit</button><button class="primary" id="done-apply">Done</button></div>');
        $('#done-apply').onclick=()=>{closeDialog();$('#editor').focus()};$('#undo-apply').onclick=async()=>{try{const u=await api('/api/apply',prior);state.source=u.sql;state.sourceHash=u.source_hash;state.context=u.context_hash;$('#editor').value=u.sql;state.revision++;paintCode();closeDialog();toast('Source edit undone. No other file was changed.')}catch(e){toast(e.message)}};
    }catch(e){toast(e.message)}};
}
function connectionDialog(){
    const c=state.p.capabilities;
    dialog('Execution is explicit','<div class="kv"><span>Local replay</span><strong>Ready · SQLite · fixed registered inputs</strong></div><div class="kv"><span>dbt Core</span><strong>'+esc(c.dbt?'Explicitly enabled for this service':'Not enabled')+'</strong></div><div class="kv"><span>BigQuery</span><strong>'+esc(c.bigquery?'Explicitly enabled for this service':'Not enabled · no warehouse request sent')+'</strong></div><div class="kv"><span>Repository writes</span><strong>'+esc(state.p.writable?'Explicit apply is enabled':'Read-only; scratch editing remains available')+'</strong></div><div class="kv"><span>Project root</span><code>'+esc(state.p.root)+'</code></div><div class="kv"><span>Native client</span><strong>Separate integration · build not verified here</strong></div><p>External projects are registered with <code>--project /path</code>. Configure trusted dbt and BigQuery settings in the project profile and authorize each integration at service startup. Compile can run project macros and contact the warehouse. Live typing never does.</p>'+(c.dbt?'<button class="button" id="core-compile">Compile saved model with Core</button>':'')+(c.bigquery?'<button class="button" id="bq-plan">Plan this GoogleSQL query</button>':''));
    if($('#core-compile'))$('#core-compile').onclick=async()=>{try{const r=await api('/api/core',{project:state.p.id,model:state.m.name,context_hash:state.context,action:'compile'});dialog('Core compilation receipt','<pre>'+pretty(r)+'</pre>')}catch(e){toast(e.message)}};
    if($('#bq-plan'))$('#bq-plan').onclick=async()=>{try{const r=await api('/api/bigquery/plan',{project:state.p.id,sql:$('#editor').value,context_hash:state.context});dialog('BigQuery dry run · not executed','<pre>'+pretty(r)+'</pre><p>Executing this approved plan is a separate cost-bearing action. Maximum bytes are enforced.</p><button class="primary" id="bq-execute">Execute approved plan</button>');$('#bq-execute').onclick=async()=>{try{const out=await api('/api/bigquery/execute',{project:state.p.id,plan_id:r.plan_id});dialog('BigQuery execution receipt','<pre>'+pretty(out)+'</pre>')}catch(e){toast(e.message)}}}catch(e){toast(e.message)}};
}
function download(name,data){const url=URL.createObjectURL(new Blob([data],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function receiptDialog(r){dialog('Execution receipt','<p>An observation is valid for its recorded code and inputs. It is not silently re-labelled after edits.</p>'+['id','kind','created_at','context_hash','sql_hash','engine','scope','status'].filter(k=>r[k]!==undefined).map(k=>'<div class="kv"><span>'+esc(k.replaceAll('_',' '))+'</span><code>'+esc(r[k])+'</code></div>').join('')+'<div class="dialog-actions"><button class="button" id="export-receipt">Export this receipt</button></div>');$('#export-receipt').onclick=()=>download('rowdy-receipt-'+r.id+'.json',JSON.stringify(r,null,2))}
function commands(){
    dialog('Jump to something useful','<input id="command-filter" aria-label="Search commands" placeholder="Model, action, or evidence…" autocomplete="off"><div id="command-items">'+state.p.models.map(m=>'<button class="option" data-command-model="'+esc(m.name)+'"><strong>'+esc(m.name)+'</strong><kbd>SQL</kbd></button>').join('')+[['Run query','run','⌘ ↵'],['Toggle live replay','live','⌘ ⇧ L'],['Verify independent checks','verify','⌘ ⇧ ↵'],['Trace an ID','trace-dialog','⌖'],['Review source changes','review','⌘ S'],['Saved evidence','history','◷']].map(([label,action,key])=>'<button class="option" data-command-action="'+action+'"><strong>'+label+'</strong><kbd>'+key+'</kbd></button>').join('')+'</div>');
    $('#command-filter').oninput=e=>$$('#command-items .option').forEach(b=>b.hidden=!b.textContent.toLowerCase().includes(e.target.value.toLowerCase()));$('#command-filter').focus();
}
function doAction(action){
    if(action==='run')return run();if(action==='verify')return run(true);if(action==='live'){if(state.live)pause();else{state.live=true;$('#live').classList.add('on');$('#live').setAttribute('aria-pressed','true');run()}return}
    if(action==='receipt'&&state.result)return receiptDialog(state.result);
    if(action==='trace-dialog')return traceDialog();if(action==='history')return history();if(action==='review')return reviewFile();
    if(action==='pin'){state.baseline=state.result;toast('Pinned this exact execution as the comparison baseline.');render();return}
    if(action==='clear-watch'){state.event=null;render();return}
    if(action==='coverage'){dialog('Coverage is not the same as absence','<p>Only the listed sources were searched. A missing record is bounded by namespace, time and input snapshot.</p>'+state.trace.coverage.map(c=>'<div class="kv"><span>'+esc(c.name)+'</span><code>'+esc(c.status.replaceAll('_',' '))+(c.matching_representations!=null?' · '+c.matching_representations+' representations':'')+'</code></div>').join('')+'<p>No connected application-log or historical warehouse service is implied.</p>');return}
    if(action==='identities'){dialog('Identity exceptions · not merged','<p>Conflicting device/session/time mappings do not establish a person. These records were kept unresolved, not assigned to this user.</p><pre>'+pretty(state.trace.unresolved)+'</pre>');return}
    if(action==='event-record'){dialog('One event, several representations','<p>'+esc(state.event.reason.detail)+'</p>'+state.event.stages.map(s=>'<h3>'+esc(s.name)+' '+badge(s.state==='found'?s.kind:'not found in scope')+'</h3>'+grid({rows:s.rows,columns:s.rows.length?Object.keys(s.rows[0]):[]},{numbered:false})).join(''));return}
}

document.addEventListener('click',async e=>{
    const b=e.target.closest('button');if(!b)return;
    try{
        if(b.dataset.model)return openModel(b.dataset.model);
        if(b.dataset.tab)return setTab(b.dataset.tab);
        if(b.dataset.action)return doAction(b.dataset.action);
        if(b.dataset.commandModel){closeDialog();return openModel(b.dataset.commandModel)}
        if(b.dataset.commandAction){const a=b.dataset.commandAction;closeDialog();return doAction(a)}
        if(b.dataset.project){const p=state.boot.projects.find(p=>p.id===b.dataset.project);closeDialog();return project(p)}
        if(b.dataset.traceField){const row=state.result.preview.rows[Number(b.dataset.traceRow)],ns=row[state.p.trace.namespace];if(ns==null){toast('The result must project its namespace. Start an explicitly scoped trace instead.');return traceDialog()}return traceValue(b.dataset.traceField,row[b.dataset.traceField],ns,state.result.id)}
        if(b.dataset.event){state.event=state.trace.events.find(x=>x.id===b.dataset.event);renderTrace();$('.event-detail')?.scrollIntoView({block:'nearest',behavior:'smooth'});return}
        if(b.dataset.editModel){await openModel(b.dataset.editModel,true);state.tab='data';await run();$('#evidence-content').scrollTop=0;$('#editor').focus();const n=$('#editor').value.indexOf('schema_version =');if(n>=0)$('#editor').setSelectionRange(n,n+'schema_version = 1'.length);caret();return}
        if(b.dataset.stageRecord!==undefined){const s=state.event.stages[Number(b.dataset.stageRecord)];dialog(s.name+' · '+s.table,'<p>'+esc(s.kind==='related_aggregate'?'Related aggregate: not the same event grain.':'Observed match on the registered namespace and event key.')+'</p>'+grid({rows:s.rows,columns:s.rows.length?Object.keys(s.rows[0]):[]},{numbered:false}));return}
        if(b.dataset.change!==undefined){const c=state.displayDiff.changes[Number(b.dataset.change)];dialog('Inspect '+c.kind+' record','<p>Key: '+c.key.map(esc).join(' / ')+'</p><div class="diff-code"><div><h3>BEFORE</h3><pre>'+pretty(c.before||'Not present')+'</pre></div><div><h3>AFTER</h3><pre>'+pretty(c.after||'Not present')+'</pre></div></div>');return}
        if(b.dataset.history){const r=await api(q('/api/receipt',{id:b.dataset.history}));closeDialog();pause(false);if(r.kind==='trace'){state.trace=r;state.event=null;state.tab='trace';render();status('Pinned historical trace · no lookup rerun.','stale')}else if(r.output){if(r.model!==state.m.name)await openModel(r.model);state.result=r;state.tab='data';render();status('Historical execution · its recorded code and inputs have not changed.','stale')}else receiptDialog(r);return}
    }catch(err){toast(err.message)}
});
document.addEventListener('change',e=>{if(e.target.id==='preview-stage'){state.stage=e.target.value;run()}if(e.target.id==='trace-clock'){state.clock=e.target.value;renderTrace()}});
document.addEventListener('input',e=>{if(e.target.id==='filter'){state.tableFilter=e.target.value;const cursor=e.target.selectionStart;render();$('#filter').focus();$('#filter').setSelectionRange(cursor,cursor)}});
$('#editor').addEventListener('input',changed);$('#editor').addEventListener('scroll',syncScroll);$('#editor').addEventListener('select',caret);$('#editor').addEventListener('click',caret);$('#editor').addEventListener('keyup',caret);
$('#editor').addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();const t=e.target;t.setRangeText('    ',t.selectionStart,t.selectionEnd,'end');changed()}});
$('#run').onclick=()=>run();$('#live').onclick=()=>doAction('live');$('#verify').onclick=()=>run(true);$('#file-diff').onclick=reviewFile;
$('#watch-selection').onclick=()=>{const e=$('#editor');state.expression=e.value.slice(e.selectionStart,e.selectionEnd);state.tab='data';run()};
$('#dialog-close').onclick=closeDialog;$('#dialog').addEventListener('cancel',()=>state.returnFocus?.focus?.());
$('#project-switch').onclick=()=>dialog('Your registered projects',state.boot.projects.map(p=>'<button class="option" data-project="'+esc(p.id)+'"><div><strong>'+esc(p.title)+'</strong><small>'+esc(p.description)+'<br>'+p.models.length+' actual SQL files · '+esc(p.data_mode)+' inputs</small></div><span>↗</span></button>').join('')+'<p>Register another local project with <code>--project /absolute/path</code>. Profiles, not engine edits, define its tables, identifiers and expectations.</p>');
$('#connection-open').onclick=connectionDialog;$('#trace-open').onclick=traceDialog;$('#history-open').onclick=history;$('#command-open').onclick=commands;
$('#home').onclick=()=>{$('#editor').focus()};$('#theme').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('rowdy.theme',document.documentElement.dataset.theme)};
$('#sidebar-close').onclick=()=>document.body.classList.add('sidebar-hidden');$('#sidebar-open').onclick=()=>document.body.classList.remove('sidebar-hidden');
$('#expand-evidence').onclick=()=>$('#split').classList.toggle('evidence-expanded');
$('#help').onclick=()=>dialog('Keep your hands on the keyboard',[['Run once','⌘ / Ctrl + Enter'],['Toggle live replay','⌘ / Ctrl + Shift + L'],['Verify','⌘ / Ctrl + Shift + Enter'],['Jump to','⌘ / Ctrl + K'],['Review source changes','⌘ / Ctrl + S'],['Close inspector','Escape']].map(([k,v])=>'<div class="kv"><span>'+k+'</span><code>'+v+'</code></div>').join('')+'<p>Continuous replay is opt-in. Opening a project or moving your caret never submits warehouse work.</p>');
$('#more').onclick=()=>dialog('More, when you need it','<button class="option" data-command-action="review"><strong>Review actual file changes</strong><span>↗</span></button><button class="option" data-command-action="history"><strong>Inspect saved evidence</strong><span>↗</span></button><button class="option" id="export-sql"><strong>Export this SQL buffer</strong><span>↓</span></button>');
document.addEventListener('click',e=>{if(e.target.closest('#export-sql')){download(state.m.name+'.sql',$('#editor').value);closeDialog()}});
document.addEventListener('keydown',e=>{if($('#dialog').open)return;if(!(e.metaKey||e.ctrlKey))return;if(e.key==='Enter'){e.preventDefault();run(e.shiftKey)}if(e.key.toLowerCase()==='k'){e.preventDefault();commands()}if(e.key.toLowerCase()==='s'){e.preventDefault();reviewFile()}if(e.shiftKey&&e.key.toLowerCase()==='l'){e.preventDefault();doAction('live')}if(e.key.toLowerCase()==='b'){e.preventDefault();document.body.classList.toggle('sidebar-hidden')}});
let resizing=false;
$('#resize').onpointerdown=e=>{resizing=true;e.target.setPointerCapture(e.pointerId)};
$('#resize').onpointermove=e=>{if(!resizing)return;const r=$('#split').getBoundingClientRect(),percent=Math.max(32,Math.min(68,(e.clientX-r.left)/r.width*100));$('#split').style.setProperty('--editor-width',percent+'%')};
$('#resize').onpointerup=()=>resizing=false;$('#resize').onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const current=parseFloat($('#split').style.getPropertyValue('--editor-width'))||51;$('#split').style.setProperty('--editor-width',Math.max(32,Math.min(68,current+(e.key==='ArrowLeft'?-3:3)))+'%')}};
document.documentElement.dataset.theme=localStorage.getItem('rowdy.theme')||'dark';
bootstrap().catch(e=>{status(e.message,'stale');$('#evidence-content').innerHTML='<div class="error-box">'+esc(e.message)+'</div>'});
