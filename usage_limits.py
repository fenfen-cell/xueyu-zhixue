"""Persistent shared-demo quotas. Never stores prompts, images or API keys."""
from contextlib import contextmanager,closing
from contextvars import ContextVar
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json,os,sqlite3,uuid,math
_scope=ContextVar('model_usage_scope',default=None)

@contextmanager
def scope(user,folder):
    token=_scope.set((user,Path(folder)))
    try:yield
    finally:_scope.reset(token)

def limited(user):return user==os.getenv('XY_DEMO_ACCOUNT','mobile_teacher')
def day():return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
def limits():
    return {'requests':int(os.getenv('XY_DEMO_DAILY_REQUESTS','20')),'tokens':int(os.getenv('XY_DEMO_DAILY_TOKENS','120000')),
        'budget_nano':int(float(os.getenv('XY_DEMO_DAILY_USD','0.20'))*1e9),'jobs':int(os.getenv('XY_DEMO_DAILY_JOBS','10')),'uploads':int(os.getenv('XY_DEMO_DAILY_UPLOADS','6'))}

@contextmanager
def database(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(folder/'usage.sqlite3',timeout=20)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,user TEXT,day TEXT,kind TEXT,tokens INTEGER,cost INTEGER,status TEXT,at TEXT)')
        with db:yield db

def totals(db,user):
    rows=db.execute('SELECT kind,count(*),COALESCE(sum(tokens),0),COALESCE(sum(cost),0) FROM usage WHERE user=? AND day=? GROUP BY kind',(user,day())).fetchall()
    result={'requests':0,'tokens':0,'cost_nano':0,'jobs':0,'uploads':0}
    for kind,count,tokens,cost in rows:
        if kind=='model':result.update(requests=count,tokens=tokens,cost_nano=cost)
        elif kind in {'jobs','uploads'}:result[kind]=count
    return result

def status(user,folder):
    with database(folder) as db:used=totals(db,user)
    cap=limits()
    return {'limited':limited(user),'day':day(),'used':used,'limits':cap,'estimated_usd':used['cost_nano']/1e9,
        'note':'北京时间每日重置。Token和费用在请求前保守预留，返回usage后结算；失败请求可能保留预留。费用按配置费率估算，以DeepSeek账单为准。'}

def consume(kind):
    current=_scope.get()
    if not current or not limited(current[0]):return
    user,folder=current;cap=limits()
    with database(folder) as db:
        db.execute('BEGIN IMMEDIATE');used=totals(db,user)
        if used[kind]>=cap[kind]:raise ValueError('共享演示账号今日'+('大模型任务' if kind=='jobs' else '导入资料')+'次数已用完，请明天再试或使用教师私有账号')
        db.execute('INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,user,day(),kind,0,0,'used',datetime.now(timezone.utc).isoformat()))

def reserve(body):
    current=_scope.get()
    if not current or not limited(current[0]) or not isinstance(body,dict) or 'messages' not in body:return None
    user,folder=current
    if body.get('model')!='deepseek-flash':raise ValueError('共享演示账号仅开放DeepSeek Flash；其他模型请使用教师私有账号')
    # Public demo images use local OCR, so only text requests reach this budget.
    for message in body.get('messages',[]):
        if isinstance(message.get('content'),list):raise ValueError('共享演示账号仅开放本地图片识别，视觉API请使用教师私有账号')
    prompt=len(json.dumps(body.get('messages',[]),ensure_ascii=False).encode('utf-8'))+4096
    output=int(body.get('max_tokens',6000));tokens=prompt+output
    cost=prompt*300+output*1200 # configured conservative USD rates: .30/1M input, 1.20/1M output
    cap=limits();ticket=uuid.uuid4().hex
    with database(folder) as db:
        db.execute('BEGIN IMMEDIATE');used=totals(db,user)
        if used['requests']>=cap['requests'] or used['tokens']+tokens>cap['tokens'] or used['cost_nano']+cost>cap['budget_nano']:
            raise ValueError('共享演示账号今日大模型额度不足，请缩小任务、明天再试或使用教师私有账号')
        db.execute('INSERT INTO usage VALUES (?,?,?,?,?,?,?,?)',(ticket,user,day(),'model',tokens,cost,'reserved',datetime.now(timezone.utc).isoformat()))
    return folder,ticket

def settle(ticket,response):
    if not ticket:return
    usage=response.get('usage',{}) if isinstance(response,dict) else {}
    a,b=usage.get('prompt_tokens'),usage.get('completion_tokens')
    if type(a) is not int or type(b) is not int or min(a,b)<0:return
    folder,ident=ticket
    with database(folder) as db:db.execute('UPDATE usage SET tokens=?,cost=?,status=? WHERE id=?',(a+b,a*300+b*1200,'settled',ident))
