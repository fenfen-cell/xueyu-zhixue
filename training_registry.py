"""Import completed, verifiable offline experiments into an append-only registry.
Publish a UI snapshot through the existing static route, without restarting the
mobile tunnel or pretending that the inference model has been switched.
"""
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
import csv,hashlib,io,json,sqlite3

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent/'藏文OCR微调'
DATABASE=ROOT/'runtime'/'training-registry.sqlite3'

def register_deployment(proof):
    from ocr_local import MODELS,selected_model
    selected=selected_model()
    folder,expected,label=MODELS[selected]
    if selected!='finetuned' or proof.get('model_id')!=selected or proof.get('model_sha256')!=expected:
        raise ValueError('线上识别响应未证明微调模型已启用')
    if sha(ROOT/'local-ocr/models'/folder/'bod.traineddata')!=expected:raise ValueError('部署权重校验失败')
    regression=read('evaluation/deployment-results.json')
    if regression['quantized_model_sha256']!=expected:raise ValueError('部署回归与当前权重不一致')
    record={'id':'deployment-'+expected[:16],'created':datetime.now(timezone.utc).isoformat(),'model_id':selected,'model_label':label,
        'model_sha256':expected,'parent_float_sha256':regression['parent_float_sha256'],'regression':regression['summary'],
        'proof':{k:proof.get(k) for k in ['model_id','model_sha256','seconds','engine','offline']},
        'note':'量化转换权重已通过真实HTTP图片识别验证。部署回归复用已观察页面，不是新的独立测试。'}
    with closing(sqlite3.connect(DATABASE)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS deployments(id TEXT PRIMARY KEY,created TEXT,data TEXT)')
        db.execute('INSERT OR IGNORE INTO deployments VALUES (?,?,?)',(record['id'],record['created'],json.dumps(record,ensure_ascii=False)))
    return record

def published_records(database=DATABASE):
    with closing(sqlite3.connect(database)) as db:
        records=[json.loads(row[0]) for row in db.execute('SELECT data FROM experiments ORDER BY imported_at DESC')]
        table=db.execute("SELECT name FROM sqlite_master WHERE name='deployments'").fetchone()
        row=db.execute('SELECT data FROM deployments ORDER BY created DESC LIMIT 1').fetchone() if table else None
    if row:
        deployment=json.loads(row[0])
        for record in records:
            if record['model_sha256']!=deployment['parent_float_sha256']:continue
            record.update(deployment_status='deployed_experimental',active_model=deployment['model_label'],deployment=deployment)
            record['timeline'].append({'at':deployment['created'],'title':'微调量化模型接入线上OCR','detail':'已通过真实HTTP图片识别验证，原模型保留，可选择或异常回退；教师复核仍待完成。'})
            record['limitations']=[x.replace('实验权重尚未接入线上OCR。','量化转换权重已接入线上，浮点实验与网页运行库结果需区分。') for x in record['limitations']]
            record['exports']['report']='# 线上接入更新\n\n'+deployment['created']+'：'+deployment['model_label']+'已接入本地OCR，原模型保留。教师审核仍待完成。\n\n以下为首次离线实验的历史报告，其中“尚未接入”描述的是当时状态。\n\n'+record['exports']['report']
    return records

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads((SOURCE/path).read_text(encoding='utf-8'))
def stamp(path):return datetime.fromtimestamp((SOURCE/path).stat().st_mtime,timezone.utc).isoformat()

def import_completed(source=SOURCE,database=DATABASE):
    global SOURCE
    SOURCE=Path(source)
    manifest=read('dataset/split-manifest.json');card=read('模型说明.json')
    timing=read('runs/round1-600/timing.json');pilot=read('runs/pilot-fixed/timing.json')
    validation=read('evaluation/validation-results.json');test=read('evaluation/test-results.json');frozen=read('evaluation/frozen-experiment.json')
    if sha(SOURCE/'runs/round1-600/bod.traineddata')!=card['sha256'] or card['sha256']!=frozen['model_hashes']['round1_600']:
        raise ValueError('实验权重哈希与评测锁定记录不一致，拒绝导入')
    if sha(SOURCE/'dataset/split-manifest.json')!=frozen['split_manifest_sha256']:
        raise ValueError('数据划分与测试锁定记录不一致，拒绝导入')
    experiment_id='xyocr-20261031-600-'+card['sha256'][:12]
    paths=['dataset/split-manifest.json','模型说明.json','runs/round1-600/timing.json','runs/round1-600/training.log',
        'evaluation/validation-results.json','evaluation/test-results.json','evaluation/frozen-experiment.json']
    evidence={path:sha(SOURCE/path) for path in paths}
    report=(SOURCE/'微调实验报告.md').read_text(encoding='utf-8')
    # Public report uses logical artifact names, not the user's physical folders.
    report=report.replace(manifest['archive'],'OCR数据集.zip').replace('D:\\学习记录\\OCR数据集.zip','OCR数据集.zip')
    output=io.StringIO(newline='');writer=csv.writer(output);writer.writerow(['page_id','split','group_id','image_sha256','previously_used_in_demo'])
    for p in manifest['pages']:writer.writerow([p['page_id'],p['split'],p['group_id'],p['image_sha256'],p['previously_used_in_demo']])
    record={'id':experiment_id,'title':'藏文OCR第一轮CPU微调','experiment_date':'2026-10-31',
        'training_status':'completed','deployment_status':'not_deployed','human_review':'pending',
        'active_model':'现有预训练量化模型（Tesseract.js）','model_sha256':card['sha256'],
        'split':manifest['counts'],'seed':manifest['seed'],'duplicate_groups':manifest['near_duplicates'],
        'prior_seen_pages':manifest['prior_demo_pages_forced_to_train'],
        'samples':timing['sample_counts'],'iterations':timing['iterations_requested'],
        'training_seconds':timing['training_seconds'],'peak_process_mib':timing['peak_process_mib'],'cpu_only':True,
        'validation':validation['summary'],'test':test['summary'],'test_pages':test['pages'],
        'metric':'与配套TXT的NFC字符编辑距离，去除全部空白；保留藏文音节点和标点。参考TXT及自动对齐样本尚未通过教师审定。',
        'selected_by':'仅依据验证集选择600步模型，锁定后评测10页独立测试；未把训练集成绩当作测试结果。',
        'limitations':['自动切行与TXT配对尚待人工复核。','少量罕见字符组合无法编码，训练日志记录了跳过项。',
            '原生引擎与现有Agent运行环境不同；实验权重尚未接入线上OCR。','测试页已用于本轮评测，后续调参需要新的最终测试数据。'],
        'timeline':[
            {'at':stamp('dataset/split-manifest.json'),'title':'整页划分与防泄漏检查','detail':'100页按80/10/10划分；24与50页归为同组；先前演示页固定在训练集。'},
            {'at':stamp('work/all-lines.json'),'title':'逐行候选生成','detail':'2126条训练候选、266条验证候选；生成可用训练文件2007条、验证文件251条。'},
            {'at':stamp('runs/pilot-fixed/timing.json'),'title':'50步CPU试跑成功','detail':f'训练{pilot["training_seconds"]:.2f}秒，训练进程峰值{pilot["peak_process_mib"]:.2f} MiB。'},
            {'at':stamp('runs/round1-600/timing.json'),'title':'600步CPU微调完成','detail':f'训练{timing["training_seconds"]:.2f}秒，训练进程峰值{timing["peak_process_mib"]:.2f} MiB。'},
            {'at':stamp('evaluation/frozen-experiment.json'),'title':'验证集选择并锁定模型','detail':'候选为50步和600步微调；仅依据验证集选择600步版本，并锁定权重与划分哈希。'},
            {'at':stamp('evaluation/test-results.json'),'title':'独立测试完成','detail':'同一原生引擎下，10页测试文字差异率由10.81%降至10.37%；线上模型尚未切换。'}],
        'evidence_sha256':evidence,
        'exports':{'report':report,'training_log':(SOURCE/'runs/round1-600/training.log').read_text(encoding='utf-8',errors='replace'),'split_csv':output.getvalue()}}
    record['timeline'].sort(key=lambda e:e['at'])
    fingerprint=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
    database=Path(database);database.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, imported_at TEXT NOT NULL, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS import_events (id INTEGER PRIMARY KEY, experiment_id TEXT NOT NULL, at TEXT NOT NULL, source_hash TEXT NOT NULL)')
        existing=db.execute('SELECT fingerprint,data FROM experiments WHERE id=?',(experiment_id,)).fetchone()
        if existing:
            if existing[0]!=fingerprint:raise ValueError('同一实验的证据已改变，不能覆盖已有训练记录')
            return json.loads(existing[1])
        record['imported_at']=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO experiments VALUES (?,?,?,?)',(experiment_id,fingerprint,record['imported_at'],json.dumps(record,ensure_ascii=False)))
        db.execute('INSERT INTO import_events (experiment_id,at,source_hash) VALUES (?,?,?)',(experiment_id,record['imported_at'],fingerprint))
    return record

def publish():
    record=import_completed()
    records=published_records()
    file=ROOT/'web/v2.js';source=file.read_text(encoding='utf-8')
    start='// BEGIN VERIFIED TRAINING SNAPSHOT';end='// END VERIFIED TRAINING SNAPSHOT'
    data=json.dumps(records,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    block=start+'\nconst verifiedTrainingRecords='+data+';\n'+end
    if start in source:
        a=source.index(start);b=source.index(end,a)+len(end);source=source[:a]+block+source[b:]
    else:source+='\n'+block+'\n'
    file.write_text(source,encoding='utf-8')
    print('Imported/published experiment:',record['id'],'records:',len(records))

if __name__=='__main__':publish()
