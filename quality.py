"""Teacher review is explicit, version-bound and never auto-approved."""
from contextlib import closing
from pathlib import Path
import hashlib,json,sqlite3
from core import now,uid

CHECKS={'doc':{'source':'来源与适用范围','text':'原文完整与文字正确','age_fit':'学科年级适配'},
 'explain':{'translation':'藏汉译文','terms':'术语一致','grammar':'语法讲解','age_fit':'适龄表达'},
 'lesson':{'facts':'原文依据与事实','activities':'课堂活动可执行','age_fit':'目标与年级适配'},
 'quiz':{'answers':'题目答案与依据','wording':'题干和选项','age_fit':'难度与年级适配'},
 'qa':{'facts':'回答符合原文','terms':'术语正确'}}

def digest(kind,item):
    if kind=='doc':value={'paragraphs':[p['text'] for p in item['paragraphs']], 'metadata':{k:v for k,v in item.get('metadata',{}).items() if k!='review_status'}}
    else:value={'answer':item.get('answer'),'bilingual':[{k:c.get(k) for k in ['original','translation_bo','translation_zh','explanation']} for c in item.get('bilingual',[])]}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def dbpath(store):return Path(store.path).parent/'teacher-reviews.sqlite3'
def init(db):db.execute('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,kind TEXT,record_id TEXT,created TEXT,data TEXT)')
def latest(store,kind,ident):
    with closing(sqlite3.connect(dbpath(store))) as db:
        init(db);row=db.execute('SELECT data FROM reviews WHERE kind=? AND record_id=? ORDER BY created DESC LIMIT 1',(kind,ident)).fetchone()
    return json.loads(row[0]) if row else None

def queue(store):
    result=[]
    documents=[store.get('docs',d['id']) for d in store.list_docs()]
    for kind,items in [('doc',documents),('run',store.list_runs())]:
        for item in items:
            checks=CHECKS['doc' if kind=='doc' else item.get('intent','qa')]
            signature=digest(kind,item);last=latest(store,kind,item['id'])
            result.append({'kind':kind,'id':item['id'],'title':item.get('title') or item.get('goal'),
                'content':'\n\n'.join(p['text'] for p in item['paragraphs']) if kind=='doc' else item.get('answer',''),
                'checks':checks,'digest':signature,'last_review':last,
                'evidence':item.get('citations',[]) if kind=='run' else [],
                'answer_key':store.get('quizzes',item['quiz']['id']) if kind=='run' and item.get('quiz') else None,
                'image_doc_id':(item['id'] if item.get('image_asset') else item.get('reference_ocr_doc_id')) if kind=='doc' else None,
                'status':last['decision'] if last and last['digest']==signature else 'pending'})
    return result

def summary(store):
    rows=queue(store)
    return {'total':len(rows),'approved':sum(r['status']=='approved' for r in rows),
        'changes_requested':sum(r['status']=='changes_requested' for r in rows),
        'pending':sum(r['status']=='pending' for r in rows),
        'note':'审核由教师专用账号持有者提交，系统不验证其职业身份；内容变更后原审核不再适用。'}

def review(store,reviewer,body):
    kind=body.get('kind');ident=body.get('id');decision=body.get('decision');alias=str(body.get('reviewer_alias','')).strip()
    if kind not in {'doc','run'} or decision not in {'approved','changes_requested'}:raise ValueError('复核类型或结论无效')
    if not 2<=len(alias)<=30:raise ValueError('请填写2—30字复核人代号，演示中不使用教师真实姓名')
    item=store.get('docs' if kind=='doc' else 'runs',ident);signature=digest(kind,item)
    if body.get('digest')!=signature:raise ValueError('内容已改变，请重新查看后复核')
    required=CHECKS['doc' if kind=='doc' else item.get('intent','qa')];checks=body.get('checks',{})
    if not isinstance(checks,dict):raise ValueError('复核清单无效')
    if decision=='approved' and not all(checks.get(k) is True for k in required):raise ValueError('通过前需逐项核对全部清单')
    note=str(body.get('note','')).strip()[:3000]
    if decision=='changes_requested' and not note:raise ValueError('请填写需要修改的内容')
    data={'id':uid(),'kind':kind,'record_id':ident,'created':now(),'reviewer_account':reviewer,
        'reviewer_alias':alias,'decision':decision,'checks':{k:checks.get(k) is True for k in required},'note':note,'digest':signature}
    with closing(sqlite3.connect(dbpath(store))) as db,db:
        init(db);db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)',(data['id'],kind,ident,data['created'],json.dumps(data,ensure_ascii=False)))
    item['teacher_review']={k:data[k] for k in ['decision','reviewer_alias','created','note','digest']}
    if kind=='doc':item.setdefault('metadata',{})['review_status']='reviewed' if decision=='approved' else 'pending'
    else:
        item['review_required']=decision!='approved'
        for card in item.get('bilingual',[]):card['reviewed']=decision=='approved'
    store.save('docs' if kind=='doc' else 'runs',item)
    return data
