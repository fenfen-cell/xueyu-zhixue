'use strict';
const demoCasesPanel=document.createElement('article');demoCasesPanel.className='panel demo-cases-panel';
$('view-studio').querySelector('.page-heading').after(demoCasesPanel);
const readinessPanel=document.createElement('article');readinessPanel.className='panel readiness-panel';readinessPanel.id='readiness-panel';
$('view-settings').querySelector('.page-heading').after(readinessPanel);
let accountRole24='',reviewQueue24=[];

async function loadDemoCases24(){
  const cases=await api('/api/demo-cases');
  demoCasesPanel.innerHTML='<h2>已保存的完整演示案例</h2><p class="tiny">可按顺序回看原图、参考文本、模型讲解、练习、示范批改及教案。教师复核结论单独记录。</p>'+
    (cases.length?cases.map(c=>`<section class="saved-demo-case"><h3>${esc(c.title)}</h3><p class="tiny">${esc(c.note)}</p><div class="probe-buttons">${c.steps.map(s=>`<button type="button" class="btn soft" data-case-kind="${esc(s.kind)}" data-case-id="${esc(s.id)}">${esc(s.label)}</button>`).join('')}</div></section>`).join(''):'<p class="muted">当前账号没有预置案例。教师私有账号可在“接入与评测”复核共享演示内容。</p>');
  demoCasesPanel.querySelectorAll('[data-case-kind]').forEach(button=>button.onclick=safe(async()=>{
    const id=button.dataset.caseId,kind=button.dataset.caseKind;
    if(kind==='doc'){view('library');await loadDocs(id);await showDocument(id);$('document-detail').scrollIntoView({behavior:'smooth',block:'start'});}
    if(kind==='run'){const run=await api('/api/runs/'+id);view('studio');showResult(run);$('result').scrollIntoView({behavior:'smooth',block:'start'});}
    if(kind==='attempt'){view('practice');await loadAttemptsV2();const item=document.querySelector(`[data-attempt="${id}"]`);if(item){item.open=true;item.scrollIntoView({behavior:'smooth',block:'start'});}}
  }));
}

async function loadReadiness24(){
  const status=await api('/api/status'),usage=await api('/api/usage'),service=await api('/api/service-status');accountRole24=status.role;
  const limited=usage.limited;
  readinessPanel.innerHTML=`<h2>模型接入、共享额度与教师复核</h2><p class="readiness-state">当前本地OCR：${esc(status.local_model||'状态待检查')}</p><p class="tiny">微调量化模型可在上传区选择，原模型保留。微调仅有小样本文字对照结果，仍需人工校对。</p>`+
    (limited?`<p class="shared-demo-notice">你正在使用共享演示空间，请仅上传公开教学资料。所有使用者共享每日额度；教师私有账号的资料独立保存。</p><div class="metrics-grid"><div>今日模型请求<span>${usage.used.requests} / ${usage.limits.requests}</span></div><div>今日Token占用<span>${usage.used.tokens} / ${usage.limits.tokens}</span></div><div>今日大模型任务<span>${usage.used.jobs} / ${usage.limits.jobs}</span></div><div>估算费用占用<span>$${usage.estimated_usd.toFixed(4)} / $${(usage.limits.budget_nano/1e9).toFixed(2)}</span></div></div><p class="tiny">今日导入 ${usage.used.uploads} / ${usage.limits.uploads} 次。${esc(usage.note)} 共享账号仅开放本地OCR，视觉API需教师私有账号。</p>`:'<p>当前为教师或独立账号空间。请保管好私有账号，不要放入公开演示截图。</p>')+
    '<h3>教师审核记录</h3><p class="tiny">机器输出不会自动变成“教师已审核”。需要教师专用账号逐项核对，并填写复核人代号和结论。系统不验证账号持有者的职业身份。</p><div id="teacher-review-content"></div>';
  const connection=document.createElement('details');connection.innerHTML='<summary>公网连接状态与恢复记录</summary>';
  const note=document.createElement('p');note.className='tiny';note.textContent='当前连接：'+(service.status.connection==='online'?'在线':service.status.connection==='reconnecting'?'正在恢复':'本机或状态待确认')+'。'+service.note;connection.append(note);
  const pre=document.createElement('pre');pre.className='teacher-review-source';pre.textContent=service.events.map(e=>e.at+' · '+({'public_verified':'公网验证通过',server_reloaded_same_url:'同网址重载成功',automatic_reconnect:'自动重连'}[e.event]||e.event)).join('\n')||'暂无连接事件';connection.append(pre);readinessPanel.append(connection);
  if(accountRole24==='teacher')await loadTeacherQueue24();
  else{const q=await api('/api/quality/status');$('teacher-review-content').innerHTML=`<p>待复核 ${q.pending} 项 · 已通过 ${q.approved} 项 · 要求修改 ${q.changes_requested} 项。</p><p class="tiny">教师专用账号保存在电脑上的使用说明文件中，不在公开登录页展示。</p>`;}
  const selector=$('ocr-provider');Array.from(selector.options).forEach(o=>{if(o.value!=='local')o.disabled=accountRole24==='demo';});
  if(accountRole24==='demo')selector.value='local';
}

async function loadTeacherQueue24(){
  reviewQueue24=await api('/api/quality/queue');
  $('teacher-review-content').innerHTML='<label for="teacher-review-item">选择待复核内容</label><select id="teacher-review-item">'+reviewQueue24.map((r,i)=>`<option value="${i}">${esc(r.status==='approved'?'已通过':r.status==='changes_requested'?'需修改':'待复核')} · ${esc(r.title)}</option>`).join('')+'</select><div id="teacher-review-detail"></div>';
  $('teacher-review-item').onchange=renderTeacherItem24;renderTeacherItem24();
}
function renderTeacherItem24(){
  const row=reviewQueue24[Number($('teacher-review-item').value)];if(!row)return;
  $('teacher-review-detail').innerHTML=`<pre class="teacher-review-source">${esc(row.content)}</pre>${row.image_doc_id?`<p><a class="text-link" href="/api/quality/image/${esc(row.image_doc_id)}" target="_blank" rel="noopener">打开原图对照 ↗</a></p>`:''}<label for="reviewer-alias">复核人代号（不填写教师真实姓名）</label><input id="reviewer-alias" maxlength="30" placeholder="例如：藏语教师A"><div class="teacher-checks">${Object.entries(row.checks).map(([k,label])=>`<label><input type="checkbox" data-quality-check="${esc(k)}">${esc(label)}</label>`).join('')}</div><label for="teacher-review-note">复核备注与修改建议</label><textarea id="teacher-review-note" rows="4"></textarea><div class="probe-buttons"><button type="button" class="btn soft" id="teacher-request-changes">记录为需要修改</button><button type="button" class="btn primary" id="teacher-approve">记录为审核通过</button></div><p class="tiny">内容变更后，原通过结论不再适用。请实际查看原文、译文和教学适用性后填写。</p>`;
  async function submit(decision){
    const checks={};document.querySelectorAll('[data-quality-check]').forEach(el=>checks[el.dataset.qualityCheck]=el.checked);
    await api('/api/quality/review',{kind:row.kind,id:row.id,digest:row.digest,decision,reviewer_alias:$('reviewer-alias').value,note:$('teacher-review-note').value,checks});
    toast('复核结论已单独留痕保存');await loadTeacherQueue24();
  }
  $('teacher-request-changes').onclick=safe(()=>submit('changes_requested'));$('teacher-approve').onclick=safe(()=>submit('approved'));
  if(row.evidence?.length||row.answer_key){const evidence=document.createElement('details');evidence.innerHTML='<summary>原文依据与教师评分参考</summary>';const pre=document.createElement('pre');pre.className='teacher-review-source';pre.textContent=JSON.stringify({原文依据:row.evidence,评分参考:row.answer_key},null,2);evidence.append(pre);$('teacher-review-detail').prepend(evidence);}
}

const previousWorkspace24=updateWorkspaceStatus;
updateWorkspaceStatus=async function(status){accountRole24=status.role;
  Array.from($('ocr-provider').options).forEach(o=>{if(o.value!=='local')o.disabled=accountRole24==='demo';});
  if(accountRole24==='demo')$('ocr-provider').value='local';
  await previousWorkspace24(status);await loadDemoCases24();};
const previousView24=view;
view=function(name){previousView24(name);if(name==='settings')safe(loadReadiness24)();if(name==='studio')safe(loadDemoCases24)();};
const previousDocument24=showDocument;
showDocument=async function(id){await previousDocument24(id);const doc=await api('/api/documents/'+id);
  if(doc.review_basis==='provided_reference'){const note=document.createElement('p');note.className='shared-demo-notice';note.textContent='此文本由配套TXT导入，仅用于技术演示，不代表人工OCR校对或教师审定。原图识别稿另外保留为待校对。';$('document-detail').prepend(note);}
  if(doc.demo_fixture&&accountRole24==='demo'){
    const note=document.createElement('p');note.className='tiny';note.textContent='预置演示资料只读。请另行上传图片或导入文字，体验自己的校对流程。';$('document-detail').prepend(note);
    $('document-detail').querySelectorAll('button').forEach(button=>button.disabled=true);
    $('document-detail').querySelectorAll('textarea,.metadata-editor input').forEach(input=>input.readOnly=true);
  }
  if(accountRole24!=='teacher'){$('meta-review')?.querySelector('option[value="reviewed"]')?.setAttribute('disabled','disabled');}
};
const previousResult24=showResult;
showResult=function(run){previousResult24(run);const note=document.createElement('p');note.className='tiny';note.textContent=run.teacher_review?.decision==='approved'?'本版本已保存教师账号持有者的复核记录。':'本结果仍待教师复核，演示记录不等于教学验证。';$('answer').before(note);
  if(accountRole24!=='teacher')$('bilingual-cards').querySelectorAll('[data-card]').forEach(b=>{b.disabled=true;b.textContent='请使用教师专用账号复核';});
};
