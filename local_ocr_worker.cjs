'use strict';
const path=require('node:path');
const {performance}=require('node:perf_hooks');
const fs=require('node:fs');
const crypto=require('node:crypto');
const {createWorker}=require('./local-ocr/node_modules/tesseract.js');
async function main(){
  const started=performance.now();
  const selected=process.argv[4]||'baseline';
  const models={baseline:['bod','0b1765dcbfef1ece5f3f425ad1eb9826024a72d71db24ca8e3e2a76a011f906f','原始预训练模型'],
    finetuned:['bod-finetuned','67e651da6a0e05a9f05224ea5f6b401bd6dc6abe987e73f745443e57c5b2393c','600步微调量化模型（待教师复核）']};
  if(!models[selected])throw Error('Unknown OCR model');
  const [folder,hash,label]=models[selected],langPath=path.join(__dirname,'local-ocr/models',folder);
  if(crypto.createHash('sha256').update(fs.readFileSync(path.join(langPath,'bod.traineddata'))).digest('hex')!==hash)throw Error('Model checksum mismatch');
  const worker=await createWorker('bod',1,{langPath,gzip:false,cacheMethod:'none'});
  let peak=process.memoryUsage().rss;
  const timer=setInterval(()=>{peak=Math.max(peak,process.memoryUsage().rss)},30);
  try{
    await worker.setParameters({tessedit_pageseg_mode:process.argv[3]||'6',user_defined_dpi:'150'});
    const {data}=await worker.recognize(process.argv[2]);
    peak=Math.max(peak,process.memoryUsage().rss);
    const lines=data.text.replace(/\r\n/g,'\n').split('\n').map(text=>text.trim()).filter(Boolean).map(text=>({text,page:1}));
    // confidence is the engine's page score, not an accuracy estimate or per-line probability.
    process.stdout.write(JSON.stringify({lines,raw_text:data.text,seconds:(performance.now()-started)/1000,
      process_peak_mib:peak/1048576,engine_confidence:data.confidence,engine:'Tesseract Tibetan CPU',psm:Number(process.argv[3]||6),offline:true,
      model_id:selected,model_label:label,model_sha256:hash}));
  }finally{clearInterval(timer);await worker.terminate();}
}
main().catch(()=>{process.stderr.write('Local OCR failed\n');process.exitCode=1;});
