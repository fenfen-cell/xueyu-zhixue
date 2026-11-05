"""Offline Tibetan CPU OCR adapter. No network requests or model downloads at runtime."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading

ROOT=Path(__file__).resolve().parent
MODEL_HASH='0b1765dcbfef1ece5f3f425ad1eb9826024a72d71db24ca8e3e2a76a011f906f'
MODELS={'baseline':('bod',MODEL_HASH,'原始预训练模型'),
        'finetuned':('bod-finetuned','67e651da6a0e05a9f05224ea5f6b401bd6dc6abe987e73f745443e57c5b2393c','600步微调量化模型（待教师复核）')}
SLOT=threading.BoundedSemaphore(1)

def selected_model(requested='auto'):
    if requested=='auto':
        try:active=json.loads((ROOT/'local-ocr/active-model.json').read_text(encoding='utf-8')).get('model','baseline')
        except (OSError,ValueError):active='baseline'
        requested=os.getenv('XY_OCR_MODEL',active)
    if requested not in MODELS:raise ValueError('未知识别模型')
    return requested


def node_path():
    configured=os.getenv('XY_NODE_EXECUTABLE')
    if configured:return Path(configured)
    bundled=ROOT/'local-ocr/node/node.exe'
    return bundled if bundled.is_file() else Path(shutil.which('node') or 'missing-node')


def status():
    model=ROOT/'local-ocr/models/bod/bod.traineddata'
    available=node_path().is_file() and model.is_file() and (ROOT/'local-ocr/node_modules/tesseract.js/src/index.js').is_file()
    chosen=selected_model()
    return {'ready':available,'engine':'Tesseract Tibetan CPU','default_psm':6,'offline':True,
            'model_id':chosen,'model_label':MODELS[chosen][2],
            'finetuned_ready':(ROOT/'local-ocr/models/bod-finetuned/bod.traineddata').is_file(),
            'message':'本地运行库与模型已准备；实际识别仍需校对' if available else '本地OCR组件不完整，请检查local-ocr目录和Node运行程序'}


def image_info(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24 and data[12:16]==b'IHDR':
        width,height=struct.unpack('>II',data[16:24]);ext,mime='png','image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        pos=2;width=height=0
        while pos<len(data)-3:
            if data[pos]!=255:pos+=1;continue
            while pos<len(data) and data[pos]==255:pos+=1
            if pos>=len(data):break
            marker=data[pos];pos+=1
            if marker in {0xd8,0xd9,0x01} or 0xd0<=marker<=0xd7:continue
            if marker==0xda:break
            length=int.from_bytes(data[pos:pos+2],'big')
            if length<2 or pos+length>len(data):break
            if marker in {0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf} and length>=8:
                height=int.from_bytes(data[pos+3:pos+5],'big');width=int.from_bytes(data[pos+5:pos+7],'big');break
            pos+=length
        ext,mime='jpg','image/jpeg'
    elif len(data)>=30 and data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        kind=data[12:16]
        if kind==b'VP8X':
            width=int.from_bytes(data[24:27],'little')+1;height=int.from_bytes(data[27:30],'little')+1
        elif kind==b'VP8L' and data[20]==0x2f:
            bits=int.from_bytes(data[21:25],'little');width=(bits&0x3fff)+1;height=((bits>>14)&0x3fff)+1
        elif kind==b'VP8 ' and data[23:26]==b'\x9d\x01\x2a':
            width=int.from_bytes(data[26:28],'little')&0x3fff;height=int.from_bytes(data[28:30],'little')&0x3fff
        else:raise ValueError('无法读取该WebP尺寸，请转为PNG或JPEG')
        ext,mime='webp','image/webp'
    else:raise ValueError('请上传PNG、JPEG或WebP图片')
    if not width or not height or width*height>16000000 or max(width,height)>12000:
        raise ValueError('图片尺寸无效或过大，请控制在1600万像素、单边12000像素以内')
    return {'extension':ext,'mime':mime,'width':width,'height':height}


def recognize(image, temporary_root, psm=6, model='auto'):
    if psm not in {3,6}:raise ValueError('页面分析模式只能选择3或6')
    info=image_info(image)
    if not status()['ready']:raise ValueError(status()['message'])
    if not SLOT.acquire(blocking=False):raise ValueError('本地OCR正在处理另一张图片，请稍后再试')
    try:
        requested=model;chosen=selected_model(requested);fallback=''
        def validate(name):
            folder,expected,_=MODELS[name];file=ROOT/'local-ocr/models'/folder/'bod.traineddata'
            return file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest()==expected
        if not validate(chosen):
            if chosen=='finetuned' and requested=='auto' and validate('baseline'):
                chosen='baseline';fallback='微调模型校验失败，已使用原模型'
            else:raise ValueError('本地OCR模型校验失败，请恢复模型文件')
        temporary_root=Path(temporary_root);temporary_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='ocr-',dir=temporary_root) as tmp:
            path=Path(tmp)/('input.'+info['extension']);path.write_bytes(image)
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            try:
                process=subprocess.run([str(node_path()),str(ROOT/'local_ocr_worker.cjs'),str(path),str(psm),chosen],
                    cwd=ROOT,stdin=subprocess.DEVNULL,capture_output=True,encoding='utf-8',timeout=120,**options)
            except subprocess.TimeoutExpired:raise ValueError('本地识别超过120秒，请缩小图片或分批处理') from None
            if process.returncode and chosen=='finetuned' and requested=='auto' and validate('baseline'):
                chosen='baseline';fallback='微调识别执行失败，已回退原模型'
                process=subprocess.run([str(node_path()),str(ROOT/'local_ocr_worker.cjs'),str(path),str(psm),chosen],cwd=ROOT,
                    stdin=subprocess.DEVNULL,capture_output=True,encoding='utf-8',timeout=120,**options)
            if process.returncode:raise ValueError('图片解码或本地OCR执行失败，请检查图片或换一种页面分析模式')
            try:result=json.loads(process.stdout)
            except ValueError:raise ValueError('本地OCR返回格式无效') from None
            if not result.get('lines'):raise ValueError('没有识别到文字，请检查图片清晰度')
            result['image_info']=info
            if fallback:result['fallback_note']=fallback
            return result
    finally:SLOT.release()
