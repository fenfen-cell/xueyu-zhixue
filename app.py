"""Run: python app.py --open. Binds to loopback only; no third-party dependencies."""
import argparse
import base64
import io
import json
import os
import re
import secrets
import threading
import webbrowser
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from xml.etree import ElementTree

from agent import Agent, ROOT, config, load_env, post_json
from core import Store, uid, public_quiz, grade_quiz, cer
from core import now
from access import Access
from connections import probe
from version import VERSION, source_revision
import ocr_local
import ocr_vision
import usage_limits
import quality

load_env()
STORE = Store(Path(os.getenv("XY_DATA_DIR", str(ROOT / "runtime"))) / "xueyu.db")
AGENT = Agent(STORE)
TOKEN = secrets.token_urlsafe(32)
AUTH = Access()
BUILD_ID = source_revision()
JOBS = {}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=3)
SLOTS = threading.BoundedSemaphore(6)


def extract_docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        part = archive.getinfo("word/document.xml")
        if part.file_size > 3 * 1024 * 1024:
            raise ValueError("DOCX 正文过大")
        xml = archive.read(part)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("不接受包含实体声明的 DOCX")
        root = ElementTree.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns))


def create_job(body, store=None, owner='local'):
    if not SLOTS.acquire(blocking=False):
        raise ValueError("任务队列已满，请等待当前任务完成")
    identifier = uid()
    with LOCK:
        # Keep active tasks; prune completed jobs before accepting another one.
        if len(JOBS) > 100:
            for old in list(JOBS):
                if JOBS[old]["status"] in {"done", "error"}:
                    del JOBS[old]
                    if len(JOBS) <= 70:
                        break
        JOBS[identifier] = {"id": identifier, "status": "running", "trace": [], 'owner':owner}
    def update(trace):
        with LOCK:
            JOBS[identifier]["trace"] = list(trace)
    def work():
        try:
            with usage_limits.scope(owner,Path(STORE.path).parent):
                result = Agent(store or STORE).run(body, update)
            with LOCK:
                JOBS[identifier].update(status="done", result=result)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else "执行失败，请查看终端或缩小任务后重试"
            if not isinstance(exc, ValueError):
                import traceback
                traceback.print_exc()
            with LOCK:
                JOBS[identifier].update(status="error", error=message)
        finally:
            SLOTS.release()
    POOL.submit(work)
    return {"job_id": identifier}


class Handler(BaseHTTPRequestHandler):
    server_version = "Xueyu/1.0"

    @property
    def user(self):
        return AUTH.current(self.headers.get('Cookie'))

    @property
    def store(self):
        user=self.user
        if not user:
            raise ValueError('请先登录')
        if not AUTH.enabled:
            return STORE
        return Store(Path(STORE.path).parent/'accounts'/AUTH.workspace(user)/'xueyu.db')

    def log_message(self, fmt, *args):
        # Avoid printing user input or API secrets.
        pass

    def reply(self, value, status=200, mime="application/json; charset=utf-8", download=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if getattr(self,'response_cookie',None):
            self.send_header('Set-Cookie',self.response_cookie)
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(raw)

    def check_host(self):
        host = self.headers.get("Host", "")
        allowed={f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        allowed.update(x.strip() for x in os.getenv('XY_ALLOWED_HOSTS','').split(',') if x.strip())
        return host in allowed

    def do_GET(self):
        if not self.check_host():
            return self.reply({"error": "Host 不受信任"}, 403)
        path = urlparse(self.path).path
        if path.startswith('/api/') and path != '/api/status' and not self.user:
            return self.reply({'error':'请先登录评审账号'},401)
        try:
            if path == "/api/status":
                cfg = config()
                return self.reply({"name": "雪域智学", "token": TOKEN, "llm_ready": bool(cfg["key"] and cfg["model"]),
                                   "model": cfg["model"] if self.user else '', "ocr_ready": ocr_local.status()['ready'] or bool(cfg["ocr"]) or bool(cfg['key'] and cfg['model']), "version": VERSION,'build_id':BUILD_ID,
                                   'local_ocr_ready':ocr_local.status()['ready'],'external_ocr_ready':bool(cfg['ocr']),
                                   'vision_ready':bool(cfg['key'] and cfg['model']),'ocr_default_provider':'local' if ocr_local.status()['ready'] else 'deepseek' if cfg['key'] and cfg['model'] else 'local',
                                   'auth_enabled':AUTH.enabled,'authenticated':bool(self.user),'user':self.user,
                                   'role':AUTH.role(self.user) if self.user else None,'local_model':ocr_local.status().get('model_label'),
                                   'capabilities_note':'配置存在不等于通过连接或质量验证'})
            if path == '/api/usage':
                return self.reply(usage_limits.status(self.user,Path(STORE.path).parent))
            if path == '/api/service-status':
                folder=Path(STORE.path).parent;state_file=folder/'state.json';events_file=folder/'connection-events.jsonl'
                state=json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {'status':'local'}
                events=[]
                if events_file.exists():
                    for line in events_file.read_text(encoding='utf-8').splitlines()[-10:]:
                        try:events.append(json.loads(line))
                        except ValueError:pass
                return self.reply({'status':{k:state.get(k) for k in ['status','url','connection','heartbeat']},'events':events,
                    'note':'后台支持断线重连和同网址重载；电脑关机、休眠或断网仍会中断。临时隧道重建后网址可能变化。'})
            if path == '/api/demo-cases':
                file=Path(self.store.path).parent/'demo-cases.json'
                return self.reply(json.loads(file.read_text(encoding='utf-8')) if file.exists() else [])
            if path.startswith('/api/runs/'):
                return self.reply(self.store.get('runs',path.split('/')[-1]))
            if path.startswith('/api/attempts/'):
                return self.reply(self.store.get('attempts',path.split('/')[-1]))
            if path == '/api/quality/queue':
                if AUTH.role(self.user)!='teacher':return self.reply({'error':'请使用教师专用账号复核'},403)
                target=os.getenv('XY_DEMO_ACCOUNT','mobile_teacher')
                target_store=Store(Path(STORE.path).parent/'accounts'/AUTH.workspace(target)/'xueyu.db') if AUTH.enabled else self.store
                return self.reply(quality.queue(target_store))
            if path.startswith('/api/quality/image/'):
                if AUTH.role(self.user)!='teacher':return self.reply({'error':'请使用教师专用账号复核'},403)
                target=os.getenv('XY_DEMO_ACCOUNT','mobile_teacher')
                folder=Path(STORE.path).parent/'accounts'/AUTH.workspace(target) if AUTH.enabled else Path(self.store.path).parent
                doc=Store(folder/'xueyu.db').get('docs',path.split('/')[-1]);asset=doc.get('image_asset','')
                if not re.fullmatch(r'[a-f0-9]{12}\.(png|jpg|webp)',asset):raise ValueError('该资料没有原图')
                return self.reply((folder/'images'/asset).read_bytes(),mime=doc.get('image_mime','image/png'))
            if path == '/api/quality/status':
                return self.reply(quality.summary(self.store))
            if path == '/api/attempts':
                return self.reply(self.store.attempts())
            if path == '/api/metrics':
                runs=self.store.list_runs()
                attempts=self.store.attempts()
                return self.reply({'runs':len(runs),'run_window':'最近40次任务','attempts':len(attempts),
                    'attempt_window':'最近200次提交','mean_score':round(sum(a['score'] for a in attempts)/len(attempts),1) if attempts else None,
                    'mean_duration_ms':round(sum(r['duration_ms'] for r in runs)/len(runs)) if runs else None,
                    'citation_pass_rate':round(sum(r.get('citation_check_passed',False) for r in runs)/len(runs),3) if runs else None,
                    'note':'运行记录统计，不等于教学效果或翻译质量。'})
            if path == "/api/documents":
                return self.reply(self.store.list_docs())
            if path == '/api/ocr/sample':
                return self.reply((ROOT/'data/ocr_sample.png').read_bytes(),mime='image/png')
            if path.startswith('/api/images/'):
                store=self.store
                doc=store.get('docs',path.split('/')[-1])
                asset=doc.get('image_asset','')
                if not re.fullmatch(r'[a-f0-9]{12}\.(png|jpg|webp)',asset):
                    return self.reply({'error':'这份教材没有保存的原图'},404)
                image=Path(store.path).parent/'images'/asset
                if not image.is_file():return self.reply({'error':'原图文件缺失'},404)
                return self.reply(image.read_bytes(),mime=doc['image_mime'])
            if path.startswith("/api/documents/"):
                return self.reply(self.store.get("docs", path.split("/")[-1]))
            if path.startswith("/api/jobs/"):
                with LOCK:
                    job = JOBS.get(path.split("/")[-1])
                    if job and job.get('owner','local') != self.user:
                        job=None
                    return self.reply({k:v for k,v in job.items() if k!='owner'} if job else {"error": "任务不存在"}, 200 if job else 404)
            if path == "/api/runs":
                return self.reply(self.store.list_runs())
            if path.startswith("/api/export/"):
                run = self.store.get("runs", path.split("/")[-1])
                if urlparse(self.path).query == "format=md":
                    body = f"# 雪域智学教学任务\n\n任务：{run['goal']}\n\n模式：{run['mode']}\n\n{run['answer']}\n\n## 原文依据\n\n"
                    body=body.replace('\n\n模式：', '\n\n审核说明：引用编号检查仅核对来源，不保证事实或翻译准确。模型内容须教师复核。\n'+run.get('coverage_note','')+'\n\n模式：',1)
                    if run.get('explain_progress'):
                        p=run['explain_progress']
                        body=f"本次精讲范围：第{p['offset']+1}至{p['next_offset']}句，共{p['total']}句。\n\n"+body
                    body += "\n\n".join(f"[{c['id']}] {c['title']}\n\n{c['text']}" for c in run["citations"])
                    return self.reply(body.encode("utf-8-sig"), mime="text/markdown; charset=utf-8", download=f"lesson-{run['id']}.md")
                return self.reply(json.dumps(run, ensure_ascii=False, indent=2).encode("utf-8"), download=f"trace-{run['id']}.json")
            static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"), '/v2.js':('v2.js','application/javascript'), '/ocr.js':('ocr.js','application/javascript'), '/readiness.js':('readiness.js','application/javascript'), "/style.css": ("style.css", "text/css")}
            if path in static:
                name, mime = static[path]
                return self.reply((ROOT / "web" / name).read_bytes(), mime=mime + "; charset=utf-8")
            return self.reply({"error": "不存在的路径"}, 404)
        except ValueError as exc:
            return self.reply({"error": str(exc)}, 400)

    def do_POST(self):
        with usage_limits.scope(self.user,Path(STORE.path).parent):
            return self.handle_post()

    def handle_post(self):
        if not self.check_host() or not secrets.compare_digest(self.headers.get("X-XY-Token", ""), TOKEN):
            return self.reply({"error": "请求校验失败，请刷新本地页面"}, 403)
        origin = self.headers.get("Origin")
        allowed_origins={f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        if os.getenv('XY_PUBLIC_ORIGIN'):
            allowed_origins.add(os.environ['XY_PUBLIC_ORIGIN'].rstrip('/'))
        if origin and origin not in allowed_origins:
            return self.reply({"error": "Origin 不受信任"}, 403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 12 * 1024 * 1024:
                raise ValueError("请求内容需小于 12 MB")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("请求需为 JSON 对象")
            path = urlparse(self.path).path
            if path == '/api/login':
                session=AUTH.login(body.get('username'),body.get('password'),self.client_address[0])
                self.response_cookie='xy_session='+session+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800'+('; Secure' if os.getenv('XY_COOKIE_SECURE','0')=='1' else '')
                return self.reply({'ok':True})
            if not self.user:
                return self.reply({'error':'请先登录评审账号'},401)
            if usage_limits.limited(self.user) and path in {'/api/metadata','/api/review','/api/ocr/review','/api/ocr/resegment'}:
                if self.store.get('docs',body.get('doc_id')).get('demo_fixture'):
                    return self.reply({'error':'预置演示资料只读；请另行上传图片或导入文字体验校对'},403)
            if path == '/api/logout':
                AUTH.logout(self.headers.get('Cookie'))
                self.response_cookie='xy_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
                return self.reply({'ok':True})
            if path == '/api/quality/review':
                if AUTH.role(self.user)!='teacher':return self.reply({'error':'请使用教师专用账号复核'},403)
                target=os.getenv('XY_DEMO_ACCOUNT','mobile_teacher')
                target_store=Store(Path(STORE.path).parent/'accounts'/AUTH.workspace(target)/'xueyu.db') if AUTH.enabled else self.store
                return self.reply(quality.review(target_store,self.user,body))
            if path == '/api/connections/test':
                if body.get('service')=='ocr' and body.get('provider','local')=='local':
                    state=ocr_local.status()
                    return self.reply({'service':'ocr','state':'available' if state['ready'] else 'unavailable','message':state['message']+'；识别在本机完成，不调用云端接口'})
                return self.reply(probe(body.get('service')))
            if path == '/api/ocr/review':
                confirm=body.get('confirm') is True
                if confirm and body.get('attested') is not True:
                    raise ValueError('请确认已对照原图检查全部段落')
                store=self.store
                doc=store.get('docs',body['doc_id'])
                if confirm and set(body.get('texts',{}))!={p['id'] for p in doc['paragraphs']}:
                    raise ValueError('整页确认需提交全部段落')
                return self.reply(store.review_page(body['doc_id'],body.get('texts'),confirm,body.get('revision')))
            if path == '/api/ocr/resegment':
                return self.reply(self.store.resegment_page(body['doc_id'],body.get('text'),body.get('revision'),body.get('merge_lines') is True))
            if path == '/api/metadata':
                if body.get('metadata',{}).get('review_status')=='reviewed' and AUTH.role(self.user)!='teacher':
                    return self.reply({'error':'教师审核状态只能由教师专用账号填写'},403)
                if not isinstance(body.get('metadata'),dict):
                    raise ValueError('来源信息应为对象')
                return self.reply(self.store.metadata(body['doc_id'],body['metadata']))
            if path == '/api/bilingual/review':
                if AUTH.role(self.user)!='teacher':return self.reply({'error':'请使用教师专用账号确认译文'},403)
                run=self.store.get('runs',body['run_id'])
                index=int(body['index'])
                if index<0 or index>=len(run.get('bilingual',[])):
                    raise ValueError('句子不存在')
                card=run['bilingual'][index]
                for field in ['translation_zh','translation_bo','explanation']:
                    text=str(body.get(field,'')).strip()
                    if not text or len(text)>4000:
                        raise ValueError('请填写汉文、藏文和讲解，每项最多4000字')
                    card[field]=text
                card.update(reviewed=True,reviewed_at=now())
                run['answer']='\n\n'.join(f"原文：{c['original']}\n汉文：{c['translation_zh'] or '待翻译'}\n藏文：{c['translation_bo'] or '待翻译'}\n讲解：{c['explanation']}\n[{c['source_id']}]" for c in run['bilingual'])
                run['review_required']=not all(c.get('reviewed') for c in run['bilingual'])
                run.pop('teacher_review',None)
                return self.reply(self.store.save('runs',run))
            if path == '/api/retry':
                attempt=self.store.get('attempts',body['attempt_id'])
                original=self.store.get('quizzes',attempt['quiz_id'])
                wrong={item['question_id'] for item in attempt['items'] if not item['correct']}
                questions=[{**q,'id':uid()} for q in original['questions'] if q['id'] in wrong]
                if not questions:
                    raise ValueError('这次提交没有错题')
                quiz={**original,'id':uid(),'created':now(),'title':original['title']+' · 错题重练','questions':questions}
                return self.reply(public_quiz(self.store.save('quizzes',quiz)))
            if path == "/api/demo":
                demo = json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8"))
                existing = next((d for d in self.store.list_docs() if d["source"] == demo["source"]), None)
                return self.reply(self.store.get("docs", existing["id"]) if existing else self.store.import_text(demo["title"], demo["text"], demo["source"]))
            if path == "/api/import":
                usage_limits.consume('uploads')
                if usage_limits.limited(self.user) and len(self.store.list_docs())>=20:raise ValueError('共享空间最多保留20份资料，请使用教师私有账号导入更多资料')
                title = str(body.get("title", "未命名教材"))
                if "file_base64" in body:
                    raw = base64.b64decode(body["file_base64"], validate=True)
                    filename = str(body.get("filename", ""))
                    if filename.lower().endswith(".docx"):
                        text = extract_docx(raw)
                    elif filename.lower().endswith((".txt", ".md")):
                        text = raw.decode("utf-8-sig")
                    else:
                        raise ValueError("支持 UTF-8 TXT、Markdown 和 DOCX；图片请在 OCR 面板导入")
                    return self.reply(self.store.import_text(title, text, "文件导入：" + Path(filename).name))
                return self.reply(self.store.import_text(title, str(body.get("text", ""))))
            if path == "/api/review":
                return self.reply(self.store.review(body["doc_id"], body["paragraph_id"], str(body["text"])))
            if path == "/api/agent":
                if body.get('mode')=='llm':usage_limits.consume('jobs')
                return self.reply(create_job(body,self.store,self.user), 202)
            if path == "/api/grade":
                if not isinstance(body.get("answers"), dict):
                    raise ValueError("answers 需要为字典")
                return self.reply(grade_quiz(self.store, body["quiz_id"], body["answers"]))
            if path == "/api/evaluate":
                return self.reply(cer(str(body.get("reference", "")), str(body.get("hypothesis", ""))))
            if path == "/api/ocr":
                usage_limits.consume('uploads')
                if usage_limits.limited(self.user) and len(self.store.list_docs())>=20:raise ValueError('共享空间最多保留20份资料，请使用教师私有账号导入更多资料')
                cfg = config()
                image = base64.b64decode(body.get("image_base64", ""), validate=True)
                if len(image) > 8 * 1024 * 1024 or not image:
                    raise ValueError("图片需为 1 字节至 8 MB")
                info=ocr_local.image_info(image)
                provider=body.get('provider','local')
                if usage_limits.limited(self.user) and (provider!='local' or len(image)>2*1024*1024):
                    raise ValueError('共享演示账号只开放2MB以内的本地OCR；视觉API请使用教师私有账号')
                store=self.store
                if provider=='local':
                    result=ocr_local.recognize(image,Path(store.path).parent/'ocr-temp',int(body.get('psm',6)),body.get('local_model','auto'))
                elif provider=='deepseek':
                    result=ocr_vision.recognize(image)
                elif provider=='external':
                    if not cfg['ocr']:raise ValueError('外部OCR未配置；可选择本地藏文识别')
                    result = post_json(cfg["ocr"], {"image_base64": body["image_base64"], "filename": str(body.get("filename", "image"))[:160], "language": "bo+zh"}, os.getenv("OCR_API_KEY", ""))
                else:raise ValueError('未知识别来源')
                lines = result.get("lines")
                if not isinstance(lines, list) or not lines or len(lines) > 1000:
                    raise ValueError("OCR 服务需返回非空 lines 数组（详见接口说明）")
                for line in lines:
                    if not isinstance(line, dict) or not isinstance(line.get("text"), str) or not line["text"].strip() or "\n" in line["text"]:
                        raise ValueError("OCR 每个 line 需含非空单行 text")
                text = "\n\n".join(line["text"] for line in lines)
                doc=store.import_text(str(body.get("title", "图片教材")), text, "OCR", lines)
                asset=doc['id']+'.'+info['extension']
                folder=Path(store.path).parent/'images';folder.mkdir(parents=True,exist_ok=True)
                (folder/asset).write_bytes(image)
                doc.update(image_asset=asset,image_mime=info['mime'],image_size=[info['width'],info['height']],
                           raw_ocr_text=result.get('raw_text',text),ocr_revision=0,review_state='draft',
                           ocr_info={k:v for k,v in result.items() if k not in {'lines','raw_text','image_info'}})
                doc['ocr_info']['provider']=provider
                return self.reply(store.save('docs',doc))
            return self.reply({"error": "不存在的接口"}, 404)
        except (ValueError, KeyError, TypeError, UnicodeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            return self.reply({"error": str(exc)[:500]}, 400)
        except Exception:
            import traceback
            traceback.print_exc()
            return self.reply({"error": "服务执行失败，请查看终端日志"}, 500)


def main():
    parser = argparse.ArgumentParser(description="雪域智学本地 Agent")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开工作台")
    args = parser.parse_args()
    if args.host not in {'127.0.0.1','localhost'}:
        if not AUTH.enabled or not AUTH.users() or not os.getenv('XY_ALLOWED_HOSTS') or not os.getenv('XY_PUBLIC_ORIGIN','').startswith('https://') or os.getenv('XY_COOKIE_SECURE')!='1':
            parser.error('非本机监听必须启用账号、配置允许域名、HTTPS站点地址和安全Cookie；生产部署需HTTPS反向代理')
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError:
        print(f"端口 {args.port} 已占用。可运行 python app.py --port 8766 --open")
        return 1
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Xueyu Agent is running: {url}\nPress Ctrl+C to stop.", flush=True)
    if args.open:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        POOL.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
