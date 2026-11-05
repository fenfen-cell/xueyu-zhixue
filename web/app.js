'use strict';
let token = '', docs = [], activeDoc = null, currentQuiz = null, latestRun = null, busy = false;
const $ = id => document.getElementById(id);
const labels = {studio:'助教工作台',library:'教材知识库',practice:'练习与批改',logs:'执行记录',settings:'接入与评测'};
function setReadingSize(large) {
  document.documentElement.classList.toggle('large-text', large);
  $('text-size').setAttribute('aria-pressed', String(large));
  $('text-size').textContent=large?'A－ 标准字号':'A＋ 大字';
}
try { setReadingSize(localStorage.getItem('xueyu-large-text')==='true'); } catch (_) { setReadingSize(false); }
$('text-size').onclick=()=>{const large=!document.documentElement.classList.contains('large-text');setReadingSize(large);try{localStorage.setItem('xueyu-large-text',String(large));}catch(_){} };
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(text, error=false) { $('toast').textContent=text; $('toast').className=error?'error':''; $('toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('toast').hidden=true,5500); }
async function api(path, data) {
  const response=await fetch(path, data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-XY-Token':token},body:JSON.stringify(data)});
  const result=await response.json(); if(!response.ok) throw Error(result.error || '请求失败'); return result;
}
function safe(action) { return async (...args)=>{try { await action(...args); } catch(error){toast(error.message,true);} }; }
function view(name) { document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='view-'+name)); document.querySelectorAll('.nav').forEach(el=>el.classList.toggle('active',el.dataset.view===name)); $('breadcrumb').textContent='工作空间 / '+labels[name]; if(name==='logs') safe(loadRuns)(); }
document.querySelectorAll('[data-view]').forEach(el=>el.addEventListener('click',()=>view(el.dataset.view)));
$('hero-import').addEventListener('click',()=>view('library'));
async function loadDocs(selected) {
  docs=await api('/api/documents'); $('doc-count').textContent=docs.length;
  const previous=selected || $('doc-select').value;
  $('doc-select').innerHTML=docs.some(d=>d.review_state!=='draft')?docs.filter(d=>d.review_state!=='draft').map(d=>`<option value="${esc(d.id)}">${esc(d.title)}</option>`).join(''):'<option value="">请先导入教材、确认OCR整页或加载样例</option>';
  if(docs.some(d=>d.id===previous)) $('doc-select').value=previous;
  $('doc-list').innerHTML=docs.length?docs.map(d=>`<button class="doc-card" data-doc="${esc(d.id)}"><strong>${esc(d.title)}</strong><small>${d.paragraph_count} 段 · ${d.review_state==='draft'?'待校对，暂不用于教学':d.metadata?.review_status==='reviewed'?'已填写教师审核标记':'可运行任务，教学内容待教师复核'} · ${esc(d.source)}</small></button>`).join(''):'<p class="muted">暂时没有教材。可以导入文字，或体验自编样例。</p>';
  $('doc-list').querySelectorAll('[data-doc]').forEach(el=>el.addEventListener('click',safe(()=>showDocument(el.dataset.doc))));
}
async function showDocument(id) {
  const doc=await api('/api/documents/'+id); activeDoc=id; $('doc-select').value=id;
  $('document-detail').innerHTML=`<div class="divider"></div><h2>${esc(doc.title)}</h2><p class="tiny">${esc(doc.source)} · 原始文本保留，校对后用于教学</p>`+doc.paragraphs.map(p=>`<div class="paragraph ${p.reviewed?'':'unreviewed'}"><div class="meta"><span>${esc(p.id)} · 第 ${esc(p.page)} 页</span><span>${p.reviewed?'已确认':p.confidence==null?'待确认 · 无置信度':'待确认 · 置信度 '+(p.confidence*100).toFixed(0)+'%'}</span></div><textarea id="p-${esc(p.id)}" rows="3" aria-label="校对段落 ${esc(p.id)}">${esc(p.text)}</textarea>${p.text!==p.original?`<details class="original"><summary>原始文字</summary>${esc(p.original)}</details>`:''}<button class="btn soft" data-review="${esc(p.id)}">保存校对并确认</button></div>`).join('');
  $('document-detail').querySelectorAll('[data-review]').forEach(el=>el.addEventListener('click',safe(async()=>{await api('/api/review',{doc_id:id,paragraph_id:el.dataset.review,text:$('p-'+el.dataset.review).value});await showDocument(id);toast('该段已确认，可用于教学检索');})));
}
$('refresh-docs').onclick=safe(()=>loadDocs());
$('load-demo').onclick=safe(async()=>{const doc=await api('/api/demo',{});await loadDocs(doc.id);$('goal').value='教材中水循环的过程是什么？';toast('已加载自编演示材料，可以开始任务');});
$('import-text-btn').onclick=safe(async()=>{const doc=await api('/api/import',{title:$('import-title').value,text:$('import-text').value});await loadDocs(doc.id);await showDocument(doc.id);$('import-text').value='';toast('教材已保存');});
function readBase64(file) { if(file.size>8*1024*1024) return Promise.reject(Error('文件需小于 8 MB')); return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件读取失败'));reader.readAsDataURL(file);}); }
$('text-file').onchange=safe(async()=>{const file=$('text-file').files[0];if(!file)return;const doc=await api('/api/import',{title:$('import-title').value||file.name,filename:file.name,file_base64:await readBase64(file)});await loadDocs(doc.id);await showDocument(doc.id);toast('文件已导入');$('text-file').value='';});
$('ocr-btn').onclick=safe(async()=>{const file=$('image-file').files[0];if(!file)throw Error('请先选择图片');$('ocr-btn').disabled=true;try{toast('正在调用配置的 OCR 服务…');const doc=await api('/api/ocr',{title:$('import-title').value||file.name,filename:file.name,image_base64:await readBase64(file)});await loadDocs(doc.id);await showDocument(doc.id);toast('识别完成，请逐段校对确认');}finally{$('ocr-btn').disabled=false;}});
document.querySelectorAll('.capability').forEach(el=>el.onclick=()=>{$('intent').value=el.dataset.intent;$('goal').value=el.dataset.goal;$('goal').focus();});
function showTrace(trace) {
  $('trace-list').innerHTML=trace.map(t=>`<div class="timeline-item"><h3>${t.step}. ${esc(t.stage)} · ${esc(t.tool)}</h3><small>${t.elapsed_ms} ms</small><details><summary>查看输入与输出</summary><pre>${esc(JSON.stringify({input:t.input,output:t.output},null,2))}</pre></details></div>`).join('');
}
function showQuiz(quiz) {
  currentQuiz=quiz;$('quiz-empty').hidden=true;$('quiz-form').hidden=false;$('quiz-title').textContent=quiz.title;$('grade-result').innerHTML='';
  $('questions').innerHTML=quiz.questions.map((q,i)=>`<div class="question"><small>第 ${i+1} 题 · ${esc(q.difficulty)} · ${esc(q.type)}</small><p>${esc(q.prompt)}</p><label for="q-${esc(q.id)}">你的答案</label>${q.options?`<select id="q-${esc(q.id)}"><option value="">请选择</option>${q.options.map(o=>`<option value="${esc(o.value)}">${esc(o.value)} · ${esc(o.text)}</option>`).join('')}</select>`:`<input id="q-${esc(q.id)}" autocomplete="off">`}</div>`).join('');
}
function showResult(run) {
  latestRun=run;$('result-empty').hidden=true;$('result').hidden=false;$('answer').textContent=run.answer;
  $('result-meta').textContent=(run.mode==='llm'?'大模型辅助 · 教师须复核':'本地检索与模板')+' / '+run.trace.length+' 个执行步骤 / '+(run.duration_ms/1000).toFixed(2)+' 秒';
  const checks=document.createElement('p');checks.className='tiny';
  checks.textContent=(run.citation_check_passed?'引用编号检查通过':'引用编号检查未通过')+'；仅核对编号来源，不验证事实或译文正确性。'+(run.mode==='llm'?'内容与译文待教师复核。':'本地结果为原文或模板。')+' '+(run.coverage_note||'');
  $('result-meta').append(checks);
  $('citations').innerHTML=run.citations.map(c=>`<details class="citation"><summary>[${esc(c.id)}] ${esc(c.title)} · 原文</summary><p>${esc(c.text)}</p></details>`).join('');
  $('export-actions').hidden=false;$('export-md').href='/api/export/'+run.id+'?format=md';$('export-json').href='/api/export/'+run.id;
  $('go-practice').hidden=!run.quiz;if(run.quiz)showQuiz(run.quiz);showTrace(run.trace);
}
async function executeTask(payload) {
  if(busy)return;busy=true;$('run-agent').disabled=true;$('progress').hidden=false;$('progress').textContent='正在规划任务…';
  try { const job=await api('/api/agent',payload);
    for(;;){const state=await api('/api/jobs/'+job.job_id);showTrace(state.trace);const last=state.trace.at(-1);$('progress').textContent=last?`${last.stage} · ${last.tool} / 已完成 ${state.trace.length} 步`:'任务已加入队列…';if(state.status==='error')throw Error(state.error);if(state.status==='done'){showResult(state.result);toast('任务完成，可查看原文依据与执行日志');break;}await new Promise(r=>setTimeout(r,700));}
  } finally{busy=false;$('run-agent').disabled=false;$('progress').hidden=true;}
}
$('run-agent').onclick=safe(()=>executeTask({doc_id:$('doc-select').value,goal:$('goal').value,intent:$('intent').value,mode:$('run-mode').value,knowledge_point:$('knowledge-point').value,question_type:$('question-type').value,count:$('question-count').value||null}));
$('go-practice').onclick=()=>view('practice');
$('quiz-form').onsubmit=safe(async event=>{event.preventDefault();const answers={};currentQuiz.questions.forEach(q=>answers[q.id]=$('q-'+q.id).value);const result=await api('/api/grade',{quiz_id:currentQuiz.id,answers});$('grade-result').innerHTML=`<div class="grade-score">${result.score}<small> / 100</small></div><p class="tiny">${esc(result.grading)}</p>`+result.items.map((q,i)=>`<div class="grade-item ${q.correct?'':'wrong'}"><strong>第 ${i+1} 题 · ${q.correct?'正确':'需要复习'}</strong><p>你的答案：${esc(q.given||'（未作答）')}<br>原文答案：${esc(q.expected)}</p><p>${esc(q.feedback)}</p>${q.followup?`<p>巩固建议：${esc(q.followup)}</p>`:''}<details><summary>依据 [${esc(q.source_id)}]</summary><p>${esc(q.source_quote)}</p></details></div>`).join('');});
async function loadRuns(){const runs=await api('/api/runs');$('run-list').innerHTML=runs.length?runs.map(r=>`<button class="run-card" data-run="${esc(r.id)}"><strong>${esc(r.goal)}</strong><small>${new Date(r.created).toLocaleString('zh-CN')} · ${esc(r.mode)}</small></button>`).join(''):'<p class="muted">尚未执行任务。</p>';$('run-list').querySelectorAll('[data-run]').forEach(el=>el.onclick=()=>{const run=runs.find(r=>r.id===el.dataset.run);showResult(run);toast('已加载这次任务，成果可在工作台查看');});}
$('refresh-runs').onclick=safe(loadRuns);
$('cer-btn').onclick=safe(async()=>{const r=await api('/api/evaluate',{reference:$('cer-ref').value,hypothesis:$('cer-hyp').value});$('cer-result').innerHTML=`<div class="grade-score">${(r.cer*100).toFixed(2)}% <small>CER</small></div><p class="muted">编辑距离 ${r.edits} / 参考码点 ${r.reference_chars}</p>`;});
async function bootstrap(){const status=await api('/api/status');token=status.token;$('ocr-provider').value=status.ocr_default_provider||'local';if(status.llm_ready)$('run-mode').value='llm';$('mode-badge').textContent=status.llm_ready?'大模型接口已配置':'本地检索模式';$('ocr-status').textContent=status.local_ocr_ready?'默认使用本地藏文OCR，图片留在本机；也可选择DeepSeek视觉识别对比，所有结果都需校对。':status.vision_ready?'DeepSeek已配置：图片将发往模型服务，结果需校对。':'请检查模型或本地OCR配置。';$('connection-status').innerHTML=`<div class="status-row"><span>本地知识库</span><b>SQLite · 已就绪</b></div><div class="status-row"><span>大模型</span><b>${status.llm_ready?esc(status.model)+' · 已配置':'尚未配置'}</b></div><div class="status-row"><span>藏文 OCR</span><b>${status.local_ocr_ready?'本地藏文OCR就绪':status.external_ocr_ready?'外部接口已配置':'尚未配置'}</b></div><p class="connection-note">“已配置”仅表示配置存在，连接与模型效果需通过真实任务验证。</p>`;if(status.auth_enabled&&!status.authenticated){$('login-screen').hidden=false;return;} $('login-screen').hidden=true; await loadDocs();await updateWorkspaceStatus(status);}
