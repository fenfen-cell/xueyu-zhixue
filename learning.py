"""Teaching templates and sentence cards; no synthetic translation is labelled as real."""
import re


def reading_blocks(text, merge_lines=False):
    """Explicit draft restructuring, preserving text and paragraph breaks."""
    if not merge_lines:
        return [line.strip() for line in text.splitlines() if line.strip()]
    blocks = []
    for block in re.split(r'\n\s*\n', text.strip()):
        joined = ''
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            separator = '' if not joined or re.search(r'[་\u4e00-\u9fff]$', joined) else ' '
            joined += separator + line
        blocks.extend(s.strip() for s in re.split(r'(?<=[。！？།])', joined) if s.strip())
    return blocks


def sentence_cards(hits, limit=24):
    cards = []
    for hit in hits:
        for sentence in re.split(r'(?<=[。！？།])', hit['text']):
            sentence = sentence.strip()
            if sentence:
                tibetan = bool(re.search(r'[\u0f00-\u0fff]', sentence))
                cards.append({'source_id':hit['id'], 'original':sentence,
                              'translation_zh':'' if tibetan else sentence,
                              'translation_bo':sentence if tibetan else '',
                              'explanation':'尚未生成跨语言译文，请接入大模型并由教师复核。',
                              'reviewed':False})
    return cards if limit is None else cards[:limit]


def lesson_template(title, hits, goal, parent=None):
    old = (parent or {}).get('lesson_settings') or {}
    time_match = re.search(r'(\d{1,3})\s*分钟', goal)
    minutes = max(10, min(120, int(time_match[1]))) if time_match else old.get('minutes',40)
    simple = old.get('simple',False)
    if re.search(r'简单|低年级|基础|容易', goal): simple = True
    if re.search(r'深入|进阶|复杂', goal): simple = False
    times = [max(1, round(minutes*r)) for r in [.125,.25,.375]]
    times.append(minutes-sum(times))
    content = '\n'.join(f"• {h['text']} [{h['id']}]" for h in hits[:6])
    questions = '先找出关键词；再指出答案在哪一段；最后用一句话复述。' if simple else '说明本段内容；比较相邻段落的联系；引用原文解释理由。'
    text = f'《{title}》教案草案\n\n课时：{minutes} 分钟\n教学层次：'+('基础理解' if simple else '阅读与分析')
    text += f'\n版本说明：'+('根据上一版和本次要求调整。' if parent else '首次生成。')
    text += f'\n\n教材依据\n{content}\n\n教学建议（教师可调整）\n教学目标：理解所选原文、辨认术语并说明原文依据。'
    for label, detail, duration in zip(['导入','阅读','理解','巩固'], ['指出本课熟悉的关键词。','逐段阅读并标记不理解的句子。',questions,'完成练习并核对出处。'],times):
        text += f'\n{label} {duration} 分钟：{detail}'
    text += '\n\n板书：课题 → 关键词 → 对应段落。\n分层作业：基础层辨认术语；提高层复述原文；拓展层根据原文提出问题。\n本地模板支持课时和简化/进阶调整，其他自由要求请使用大模型模式。'
    return text, {'minutes':minutes,'simple':simple}
