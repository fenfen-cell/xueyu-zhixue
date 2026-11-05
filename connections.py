"""On-demand probes. Never returns secrets, remote bodies, or fabricated OCR confidence."""
import os
import time
import urllib.request
from urllib.parse import urlparse
from agent import config, post_json


def probe(service):
    cfg=config()
    started=time.monotonic()
    result={'service':service,'checked_at':time.time()}
    if service=='llm':
        if not cfg['key'] or not cfg['model']:
            return {**result,'state':'unconfigured','message':'尚未配置模型密钥和模型名称'}
        try:
            body={'model':cfg['model'],'messages':[{'role':'user','content':'Reply with READY.'}],'max_tokens':128}
            if urlparse(cfg['base']).hostname=='api.deepseek.com':body['thinking']={'type':'disabled'}
            response=post_json(cfg['base']+'/chat/completions',body,cfg['key'],timeout=20)
            msg=response['choices'][0]['message']
            if not isinstance(msg.get('content'),str) or not msg['content'].strip():
                raise ValueError('模型未返回文本')
            result.update(state='available',message='已收到真实文本响应；不代表藏文质量或工具调用已验证')
        except (ValueError,KeyError,IndexError,TypeError,OSError):
            result.update(state='unavailable',message='连接或响应检查失败，请检查地址、密钥、模型权限与网络')
    elif service=='ocr':
        if not cfg['ocr']:
            return {**result,'state':'unconfigured','message':'尚未配置 OCR 服务'}
        url=os.getenv('OCR_HEALTH_URL','')
        if not url:
            return {**result,'state':'unverified','message':'已配置识别接口；未配置健康检查地址，请上传真实图片验证'}
        if not url.startswith('https://') and not url.startswith(('http://127.0.0.1:','http://localhost:')):
            raise ValueError('OCR 健康地址必须为 HTTPS 或本机 HTTP')
        try:
            headers={'Authorization':'Bearer '+os.getenv('OCR_API_KEY')} if os.getenv('OCR_API_KEY') else {}
            with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=10) as response:
                response.read(1024)
            result.update(state='available',message='健康接口可访问；实际识别效果需上传真实图片验证')
        except (OSError,ValueError):
            result.update(state='unavailable',message='OCR 健康接口连接失败')
    else:
        raise ValueError('未知服务')
    result['elapsed_ms']=round((time.monotonic()-started)*1000)
    return result
