"""Local textbook store, Unicode-aware retrieval and assessment tools. Stdlib only."""
import collections
import json
import math
import re
import sqlite3
import unicodedata
import uuid
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def uid():
    return uuid.uuid4().hex[:12]


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    # Preserve Tibetan combining marks and tsheg; never silently rewrite spelling.
    return unicodedata.normalize("NFC", text).replace("\r\n", "\n").strip()


def tokens(text):
    text = normalize(text).lower()
    chinese = re.findall(r"[\u3400-\u9fff]+", text)
    result = re.findall(r"[a-z0-9]+|[\u0f00-\u0fff]+", text)
    result = [piece for word in result for piece in re.split(r"[་།༎\s]+", word) if piece]
    for word in chinese:
        result.extend(word)
        result.extend(word[i:i + 2] for i in range(len(word) - 1))
    return result


STOP_WORDS = set('的了是在和与及就都也吗呢啊有个这那我你他她们不请问')
QUERY_PHRASES = re.compile(r'请问|请帮我|帮我|教材中|课文中|根据|这节课|这篇课文|是什么|为什么|怎么做|如何|怎样|介绍一下|讲解|解释|过程|变化|哪些|什么')


def query_tokens(query):
    query = QUERY_PHRASES.sub('', normalize(query))
    return [t for t in tokens(query) if t not in STOP_WORDS]


def matches_knowledge(text, knowledge_point):
    points = [normalize(p).casefold() for p in re.split(r'[,，、;；\s]+',knowledge_point) if p.strip()]
    return any(p in normalize(text).casefold() for p in points)


def review_state(doc):
    if doc.get('source')!='OCR':return 'confirmed'
    return 'confirmed' if doc.get('paragraphs') and all(p.get('reviewed') for p in doc['paragraphs']) else 'draft'


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS docs(id TEXT PRIMARY KEY,title TEXT,created TEXT,data TEXT);
              CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,created TEXT,data TEXT);
              CREATE TABLE IF NOT EXISTS quizzes(id TEXT PRIMARY KEY,data TEXT);
              CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,created TEXT,data TEXT);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, table, data):
        if table not in {"docs", "runs", "quizzes", "attempts"}:
            raise ValueError("未知数据表")
        with self.db() as db:
            if table == "docs":
                db.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?)", (data["id"], data["title"], data.get("created", now()), json.dumps(data, ensure_ascii=False)))
            elif table == "quizzes":
                db.execute("INSERT OR REPLACE INTO quizzes VALUES (?,?)", (data["id"], json.dumps(data, ensure_ascii=False)))
            else:
                db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (data["id"], now(), json.dumps(data, ensure_ascii=False)))
        return data

    def get(self, table, identifier):
        if table not in {"docs", "runs", "quizzes", "attempts"}:
            raise ValueError("未知数据表")
        with self.db() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ValueError("记录不存在")
        data=json.loads(row[0])
        if table=='docs':data['review_state']=review_state(data)
        return data

    def list_docs(self):
        with self.db() as db:
            rows = db.execute("SELECT data FROM docs ORDER BY created DESC").fetchall()
        result=[]
        for row in rows:
            doc=json.loads(row[0])
            result.append({**{k:v for k,v in doc.items() if k not in {'paragraphs','raw_ocr_text','review_history'}},
                           'review_state':review_state(doc),'confirmed_count':sum(bool(p.get('reviewed')) for p in doc['paragraphs'])})
        return result

    def list_runs(self):
        with self.db() as db:
            rows = db.execute("SELECT data FROM runs ORDER BY created DESC LIMIT 40").fetchall()
        return [json.loads(r[0]) for r in rows]

    def attempts(self):
        with self.db() as db:
            rows = db.execute('SELECT data FROM attempts ORDER BY created DESC LIMIT 200').fetchall()
        return [json.loads(r[0]) for r in rows]

    def metadata(self, identifier, values):
        doc = self.get('docs', identifier)
        allowed = {'source_name', 'source_url', 'authorization', 'subject', 'grade', 'edition', 'review_status'}
        meta = dict(doc.get('metadata', {}))
        for k, v in values.items():
            if k in allowed:
                meta[k] = str(v).strip()[:500]
        if meta.get('review_status', 'pending') not in {'pending', 'reviewed'}:
            raise ValueError('审核状态无效')
        doc['metadata'] = meta
        return self.save('docs', doc)

    def import_text(self, title, text, source="手动导入", lines=None):
        text = normalize(text)
        if not text or len(text) > 100000:
            raise ValueError("教材内容需为 1–100000 字符")
        doc_id = uid()
        blocks = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
        paragraphs = []
        for i, block in enumerate(blocks):
            line = lines[i] if lines and i < len(lines) else {}
            confidence = line.get("confidence")
            if confidence is not None:
                confidence = float(confidence)
                if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError("OCR confidence 必须介于 0 与 1")
            paragraphs.append({"id": f"{doc_id}:P{i + 1}", "text": block, "original": block,
                               "page": line.get("page", 1), "bbox": line.get("bbox"),
                               "confidence": confidence, "reviewed": source != "OCR",
                               "sentences": [s for s in re.split(r"(?<=[。！？།])", block) if s.strip()]})
        return self.save("docs", {"id": doc_id, "title": title.strip()[:160] or "未命名教材", "created": now(),
                                  "source": source, "paragraph_count": len(paragraphs), "paragraphs": paragraphs})

    def review(self, doc_id, paragraph_id, text):
        return self.review_page(doc_id,{paragraph_id:text},confirm=True)

    def review_page(self,doc_id,texts,confirm=False,expected_revision=None):
        if not isinstance(texts,dict) or not texts:raise ValueError('请提交需要校对的文字')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM docs WHERE id=?',(doc_id,)).fetchone()
            if not row:raise ValueError('教材不存在')
            doc=json.loads(row[0]);revision=doc.get('ocr_revision',0)
            if expected_revision is not None and expected_revision!=revision:
                raise ValueError('校对版本已更新，请重新打开教材后再保存')
            known={p['id']:p for p in doc['paragraphs']}
            if any(k not in known for k in texts):raise ValueError('校对段落不存在')
            stamp=now()
            for pid,text in texts.items():
                if not isinstance(text,str) or not text.strip() or len(text)>10000:
                    raise ValueError('段落文字需为1–10000字符，不能留空')
                paragraph=known[pid];updated=normalize(text)
                if updated!=paragraph['text']:
                    doc.setdefault('metadata',{})['review_status']='pending'
                    history=paragraph.setdefault('edit_history',[])
                    history.append({'at':stamp,'before':paragraph['text'],'after':updated})
                    paragraph['edit_history']=history[-20:]
                paragraph.update(text=updated,reviewed=bool(confirm))
                paragraph['sentences']=[s for s in re.split(r'(?<=[。！？།])',updated) if s.strip()]
                if confirm:paragraph['reviewed_at']=stamp
            if sum(len(p['text']) for p in doc['paragraphs'])>100000:raise ValueError('教材校对文字超过100000字符')
            doc['ocr_revision']=revision+1
            doc['review_state']=review_state(doc)
            history=doc.setdefault('review_history',[])
            history.append({'at':stamp,'action':'confirm' if confirm else 'save_draft','paragraph_count':len(texts),'revision':revision+1})
            doc['review_history']=history[-100:]
            db.execute('UPDATE docs SET data=? WHERE id=?',(json.dumps(doc,ensure_ascii=False),doc_id))
        return doc

    def resegment_page(self, doc_id, text, expected_revision, merge_lines=False):
        from learning import reading_blocks
        if not isinstance(text,str) or not 1 <= len(text.strip()) <= 100000:
            raise ValueError('整页文字需为1–100000字符')
        blocks = reading_blocks(normalize(text),merge_lines)
        if not blocks or len(blocks)>1000 or any(len(b)>10000 for b in blocks):
            raise ValueError('请调整分段，每段最多10000字符，最多1000段')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM docs WHERE id=?',(doc_id,)).fetchone()
            if not row:raise ValueError('教材不存在')
            doc=json.loads(row[0])
            if doc.get('source')!='OCR':raise ValueError('此功能仅用于OCR草稿')
            revision=doc.get('ocr_revision',0)
            if type(expected_revision) is not int or expected_revision!=revision:
                raise ValueError('校对版本已更新，请重新打开教材')
            old=doc['paragraphs']
            next_number=max(int(p['id'].rsplit(':P',1)[1]) for p in old)+1
            stamp=now()
            archive=doc.setdefault('segmentation_history',[])
            archive.append({'at':stamp,'revision':revision,'paragraphs':old})
            doc['segmentation_history']=archive[-10:]
            doc['paragraphs']=[{'id':f'{doc_id}:P{next_number+i}','text':b,'original':b,'page':1,'bbox':None,
                'confidence':None,'reviewed':False,'original_kind':'resegmented','sentences':[s for s in re.split(r'(?<=[。！？།])',b) if s.strip()]} for i,b in enumerate(blocks)]
            doc.update(paragraph_count=len(blocks),review_state='draft',ocr_revision=revision+1)
            doc.setdefault('review_history',[]).append({'at':stamp,'action':'resegment_draft','revision':revision+1,'paragraph_count':len(blocks),'merge_lines':merge_lines})
            doc['review_history']=doc['review_history'][-100:]
            db.execute('UPDATE docs SET data=? WHERE id=?',(json.dumps(doc,ensure_ascii=False),doc_id))
        return doc

    def search(self, query, doc_id=None, limit=5):
        documents = [self.get("docs", doc_id)] if doc_id else [self.get("docs", d["id"]) for d in self.list_docs()]
        corpus = [(d, p, collections.Counter(tokens(p["text"]))) for d in documents if review_state(d)=='confirmed' for p in d["paragraphs"] if p["reviewed"]]
        q = set(query_tokens(query))
        if not corpus or not q:
            return []
        avg = sum(sum(c.values()) for _, _, c in corpus) / len(corpus) or 1
        df = collections.Counter(t for _, _, c in corpus for t in c)
        hits = []
        for d, p, c in corpus:
            strong = {t for t in q if len(t) >= 2}
            overlap = q.intersection(c)
            # A common single Chinese character must not trigger a textbook answer.
            if strong:
                if not strong.intersection(c) or len(strong.intersection(c)) / len(strong) < .25:
                    continue
            elif not overlap or len(overlap) / len(q) < .5:
                continue
            score = sum(math.log(1 + (len(corpus) - df[t] + .5) / (df[t] + .5)) * c[t] * 2.2 /
                        (c[t] + 1.2 * (.25 + .75 * sum(c.values()) / avg)) for t in q if c[t])
            if score > 0:
                hits.append({"id": p["id"], "title": d["title"], "text": p["text"], "page": p["page"], "score": round(score, 4)})
        return sorted(hits, key=lambda h: h["score"], reverse=True)[:limit]


def make_quiz(store, doc_id, count=3, difficulty="基础", selected=None, question_type='fill', knowledge_point=''):
    doc = store.get("docs", doc_id)
    if review_state(doc)!='confirmed':raise ValueError('教材整页尚未确认，请先完成全部段落校对')
    paragraphs = [p for p in doc["paragraphs"] if p["reviewed"]]
    if selected is not None:
        paragraphs = [p for p in paragraphs if p["id"] in selected]
    if knowledge_point:
        ids = {h['id'] for h in store.search(knowledge_point, doc_id, 20)}
        paragraphs = [p for p in paragraphs if p['id'] in ids and matches_knowledge(p['text'],knowledge_point)]
    if not paragraphs:
        raise ValueError("没有已校对的教材段落，请先确认识别结果")
    if question_type not in {'fill', 'choice', 'truefalse', 'mixed'}:
        raise ValueError('未知题型')
    count = max(1, min(int(count), 8, len(paragraphs)))
    questions = []
    for p in paragraphs[:count]:
        # Evidence-based extractive exercise; no invented Tibetan translations.
        candidates = re.findall(r"[\u4e00-\u9fff]{2,8}|[\u0f40-\u0fbc]+(?:་[\u0f40-\u0fbc]+){0,2}|[A-Za-z]{3,}", p["text"])
        answer = max(candidates, key=len) if candidates else p["text"][:6]
        masked = p["text"].replace(answer, "____", 1)
        kind = ['fill', 'choice', 'truefalse'][len(questions) % 3] if question_type == 'mixed' else question_type
        options = None
        prompt = f"根据教材补全原文：\n{masked}"
        label = '原文填空'
        if kind == 'choice':
            # All distractors are explicitly marked missing/blank options, not invented facts.
            pool = [x['text'] for x in paragraphs if x['id'] != p['id']]
            distractors = [x[:80] for x in pool[:2]]
            distractors += ['教材未提供填空内容', '此处无需填写']
            choices = [answer] + [x for x in dict.fromkeys(distractors) if x != answer][:3]
            random.SystemRandom().shuffle(choices)
            options = [{'value': chr(65+i), 'text': x} for i,x in enumerate(choices)]
            answer = next(o['value'] for o in options if o['text'] == answer)
            label, prompt = '原文选择', f'选择能准确补全原文的一项：\n{masked}'
        elif kind == 'truefalse':
            negative = len(questions) % 2 == 1
            prompt = ('判断：下列句子没有出现在所学原文中。\n' if negative else '判断：下列句子出现在所学原文中。\n') + p['text']
            answer = '错误' if negative else '正确'
            options = [{'value':'正确','text':'正确'}, {'value':'错误','text':'错误'}]
            label = '原文判断'
        questions.append({"id": uid(), "type": label, "prompt": prompt, 'options': options,
                          "answer": answer, "source_id": p["id"], "source_quote": p["text"], "difficulty": difficulty})
    return store.save("quizzes", {"id": uid(), "doc_id": doc_id, "created": now(), 'knowledge_point':knowledge_point, 'difficulty_note':'难度为教师选择的标签，未做能力校准', "title": doc["title"] + " · 随堂练习", "questions": questions})


def public_quiz(quiz):
    return {**quiz, "questions": [{k: v for k, v in q.items() if k not in {"answer", "source_quote"}} for q in quiz["questions"]]}


def grade_quiz(store, quiz_id, answers):
    quiz = store.get("quizzes", quiz_id)
    items = []
    for q in quiz["questions"]:
        answer = str(answers.get(q["id"], "")).strip()
        correct = normalize(answer).casefold() == normalize(q["answer"]).casefold()
        items.append({"question_id": q["id"], "prompt": q["prompt"], "given": answer, "expected": q["answer"],
                      "correct": correct, "source_id": q["source_id"], "source_quote": q["source_quote"],
                      'type':q['type'], 'options':q.get('options'),
                      "feedback": "与评分依据一致。" if correct else ("尚未作答，请回看原文后补全。" if not answer else "与评分依据不一致；填空按文字匹配，选择/判断按选项评分，请核对对应原文。"),
                      "followup": None if correct else "重新阅读该段，遮住答案后再复述一次。"})
    result = {"id": uid(), "quiz_id": quiz_id, 'doc_id':quiz['doc_id'], 'created':now(), "score": round(100 * sum(q["correct"] for q in items) / len(items)),
              "items": items, "grading": "填空按规范化后的原文文字匹配；选择/判断按评分选项，不用于主观题语义评分"}
    return store.save("attempts", result)


def cer(reference, hypothesis):
    a, b = normalize(reference), normalize(hypothesis)
    if not a:
        raise ValueError("CER 参考文本不能为空")
    if len(a) * len(b) > 4000000:
        raise ValueError("CER 单次文本过长，请按行评测")
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return {"edits": prev[-1], "reference_chars": len(a), "cer": round(prev[-1] / len(a), 6), "unit": "NFC Unicode code point"}
