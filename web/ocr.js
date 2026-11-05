'use strict';

async function recognizeFileLocal(file,title){
  const button=$('ocr-btn');button.disabled=true;
  $('ocr-status').textContent='正在识别，请稍候；复杂图片可能需要更长时间。';
  try{
    const doc=await api('/api/ocr',{title:title||file.name,filename:file.name,image_base64:await readBase64(file),provider:$('ocr-provider').value,local_model:$('local-model').value,psm:Number($('ocr-psm').value)});
    await loadDocs();await showDocument(doc.id);
    $('document-detail').scrollIntoView({behavior:'smooth',block:'start'});
    toast('识别完成，当前是草稿。请对照原图校对并确认整页。');
    const seconds=doc.ocr_info?.seconds;
    $('ocr-status').textContent=doc.ocr_info?.provider==='local'?`本地识别完成${seconds?'，耗时 '+seconds.toFixed(2)+' 秒':''}。未确认前不参与教学。`:doc.ocr_info?.provider==='deepseek'?`DeepSeek视觉识别完成${seconds?'，耗时 '+seconds.toFixed(2)+' 秒':''}，请对照原图校对。`:'外部识别完成，请对照原图校对。';
  }catch(error){$('ocr-status').textContent='识别失败：'+error.message;throw error;}
  finally{button.disabled=false;}
}
$('ocr-btn').onclick=safe(async()=>{const file=$('image-file').files[0];if(!file)throw Error('请先选择图片');await recognizeFileLocal(file,$('import-title').value);});
const sampleButtonOCR=document.createElement('button');sampleButtonOCR.className='btn soft';sampleButtonOCR.textContent='用测试图体验当前识别方式';sampleButtonOCR.type='button';
$('ocr-btn').after(sampleButtonOCR);
sampleButtonOCR.onclick=safe(async()=>{
  sampleButtonOCR.disabled=true;
  try{
    const response=await fetch('/api/ocr/sample');if(!response.ok)throw Error('无法读取样例，请重新登录后再试');
    const file=new File([await response.blob()],'ocr-test-75.png',{type:'image/png'});
    await recognizeFileLocal(file,'藏文识别测试图75 · 非审核教材');
  }finally{sampleButtonOCR.disabled=false;}
});

const originalShowDocumentOCR=showDocument;
showDocument=async function(id){
  await originalShowDocumentOCR(id);
  const doc=await api('/api/documents/'+id);
  document.querySelector('.library-grid').classList.toggle('ocr-layout-active',doc.source==='OCR');
  if(doc.source!=='OCR')return;
  const detail=$('document-detail'),metadata=detail.querySelector('.metadata-editor');
  detail.replaceChildren();
  const title=document.createElement('h2');title.textContent=doc.title;detail.append(title);
  const panel=document.createElement('div');panel.className='ocr-review-panel';
  const info=doc.ocr_info||{};
  panel.innerHTML=`<p class="ocr-review-status ${doc.review_state==='draft'?'pending':''}">${doc.review_state==='draft'?'待整页校对 · 当前不能用于教学':'整页已确认 · 可以用于教学'} · 校对版本 ${doc.ocr_revision||0}</p><p class="tiny">${info.provider==='local'?'本机CPU识别，未上传图片':info.provider==='deepseek'?'DeepSeek视觉识别，图片已发送模型服务':'外部OCR识别记录'}${info.seconds?' · '+info.seconds.toFixed(2)+'秒':''}。识别提示不代表准确率，请检查所有文字。</p><div class="ocr-review-grid"><section class="ocr-image-panel"><div class="panel-title"><h3>教材原图</h3>${doc.image_asset?`<a class="text-link" href="/api/images/${esc(id)}" target="_blank" rel="noopener">单独打开原图 ↗</a>`:''}</div>${doc.image_asset?`<label for="ocr-zoom">原图缩放</label><input type="range" id="ocr-zoom" min="70" max="250" value="100" aria-label="原图缩放"><div class="ocr-image-scroll"><img id="ocr-page-image" src="/api/images/${esc(id)}" alt="${esc(doc.title)}的上传原图"></div>`:'<p class="muted">旧版识别记录未保存原图。重新上传图片即可使用对照功能。</p>'}</section><section class="ocr-text-panel"><h3>识别初稿与人工校对</h3><p class="tiny">文字改动后可以先保存草稿；只有明确确认全部段落，才会进入教学范围。疑似漏行可补在相邻段落中；段落不能留空。</p>${doc.paragraphs.map((p,i)=>`<article class="ocr-line-editor"><label for="ocr-line-${i}">段落 ${i+1} · ${p.reviewed?'已确认':'待校对'}</label><textarea id="ocr-line-${i}" rows="3" data-ocr-paragraph="${esc(p.id)}">${esc(p.text)}</textarea>${p.text!==p.original?`<details class="tiny"><summary>${p.original_kind==='resegmented'?'查看重新分段时的文字':'查看这一段的原始识别稿'}</summary><pre>${esc(p.original)}</pre></details>`:''}</article>`).join('')}<div class="ocr-confirm-area"><label class="ocr-attestation"><input type="checkbox" id="ocr-attested">我已对照原图检查整页文字，包括可能的漏行、错字和阅读顺序。</label><div class="probe-buttons"><button type="button" class="btn soft" id="save-ocr-draft">保存校对草稿</button><button type="button" class="btn primary" id="confirm-ocr-page">确认整页并用于教学</button></div><p class="tiny" id="ocr-save-note">保存草稿会暂停该页用于新教学任务。此前的历史任务仍保留，可能基于旧文字。</p></div></section></div><details class="ocr-audit"><summary>原始识别全文与修改记录</summary><h3>原始识别稿</h3><pre>${esc(doc.raw_ocr_text||doc.paragraphs.map(p=>p.original).join('\n'))}</pre><h3>校对记录</h3><pre>${esc(JSON.stringify(doc.review_history||[],null,2))}</pre>${doc.paragraphs.filter(p=>p.edit_history?.length).map(p=>`<details><summary>${esc(p.id)} 文字修改历史</summary><pre>${esc(JSON.stringify(p.edit_history,null,2))}</pre></details>`).join('')}</details>`;
  detail.append(panel);if(metadata)detail.append(metadata);
  if(info.model_label){const label=document.createElement('p');label.className='tiny';label.textContent='识别模型：'+info.model_label+(info.fallback_note?'；'+info.fallback_note:'');panel.prepend(label);}
  const restructure=document.createElement('details');restructure.className='ocr-audit';
  restructure.innerHTML='<summary>调整整页分段与跨行句子</summary><p class="tiny">先保存上方尚未保存的修改，再打开此区。可粘贴配套TXT或整页校对文字，每行作为一段。也可勾选合并跨行文字后按句末标点切分；标题、缩写和特殊标点可能需要手动调整。保存后整页回到草稿，必须对照原图重新确认。旧分段和历史任务保留。</p><label for="ocr-fulltext">整页文字</label><textarea id="ocr-fulltext" rows="12"></textarea><label><input type="checkbox" id="ocr-merge-lines">合并单换行并按句末标点重新分句（空行保留边界）</label><button type="button" class="btn soft" id="ocr-resegment">保存新分段为草稿</button>';
  panel.append(restructure);
  if(doc.segmentation_history?.length){const history=document.createElement('details');history.innerHTML='<summary>查看重新分段前的文字记录</summary>';const pre=document.createElement('pre');pre.textContent=JSON.stringify(doc.segmentation_history,null,2);history.append(pre);panel.append(history);}
  $('ocr-fulltext').value=doc.paragraphs.map(p=>p.text).join('\n');
  restructure.ontoggle=()=>{if(restructure.open)$('ocr-fulltext').value=Array.from(panel.querySelectorAll('[data-ocr-paragraph]'),el=>el.value).join('\n');};
  $('ocr-resegment').onclick=safe(async()=>{
    $('ocr-resegment').disabled=true;
    try{await api('/api/ocr/resegment',{doc_id:id,text:$('ocr-fulltext').value,revision:doc.ocr_revision||0,merge_lines:$('ocr-merge-lines').checked});
      await loadDocs();await showDocument(id);toast('新分段已保存为草稿，请对照原图重新确认整页');
    }finally{if($('ocr-resegment'))$('ocr-resegment').disabled=false;}
  });
  const zoom=$('ocr-zoom');if(zoom)zoom.oninput=()=>{$('ocr-page-image').style.width=zoom.value+'%';};
  async function save(confirm){
    if(confirm&&!$('ocr-attested').checked)throw Error('请先勾选已检查整页文字');
    const texts={};panel.querySelectorAll('[data-ocr-paragraph]').forEach(el=>{texts[el.dataset.ocrParagraph]=el.value;});
    $('save-ocr-draft').disabled=true;$('confirm-ocr-page').disabled=true;
    try{
      const updated=await api('/api/ocr/review',{doc_id:id,texts,confirm,attested:confirm&&$('ocr-attested').checked,revision:doc.ocr_revision||0});
      await loadDocs(updated.review_state==='confirmed'?id:undefined);await showDocument(id);
      toast(confirm?'整页已确认，可以在工作台使用':'校对草稿已保存，暂不用于教学');
    }finally{if($('save-ocr-draft'))$('save-ocr-draft').disabled=false;if($('confirm-ocr-page'))$('confirm-ocr-page').disabled=false;}
  }
  $('save-ocr-draft').onclick=safe(()=>save(false));$('confirm-ocr-page').onclick=safe(()=>save(true));
};
