#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""故事书（书名在 family.json 的 book_title）——把妈妈的故事条目（story.py 攒的原话）写成一本可以翻的书。

每晚（daily_report 之后）按章节渲染：只重写内容有变化的章节（按条目哈希），用 DeepSeek 以非虚构的文学笔法
写成第三人称叙述；每一段都标出它依据的原话（条目 id + 第几句），前端把原话排在对页当"注"，可以点开听录音。
叙述只能用她说过的事实，不许添细节；她前后说法不一的地方，照实写"她有时说……有时说……"。
每章末尾由模型提出 2-3 个"想听更多"的线头，自动进题库，以后 AI 会去问。

产物：DATA_DIR/story/book.json
"""
import hashlib
import json
import os
import time
import traceback

import deepseek_client
import family
import story

BOOK_PATH = os.path.join(story.STORY_DIR, "book.json")
TITLE = family.BOOK_TITLE
SUBTITLE = f"{family.ELDER_NAME}口述 · 她的儿子{family.CHILD_NAME}整理"

CHAPTER_PROMPT = """你在写一本关于我母亲{elder_name}（{elder_brief}，{elder_birth}生）的书，书名《{book_title}》。
材料是她平时和儿子"{child}"（AI 替身）聊天时讲的往事，下面是「{chapter}」这一章的全部材料：每条有编号，
每条里是她的原话（逐句，语音识别的，有错字、重复、断句错），后面是一句概括，有的还有她后来核实时的态度和更正。

请把这一章写成书里的一章：
- 文学笔法，非虚构：像好的口述史整理者那样写，第三人称（"她"；第一次出现用"{elder_name}"或"母亲"），克制、温暖、具体，有画面，不煽情，不评价，不总结人生道理。
- **只能用材料里她说过的事实**。一个人名、地名、年份、物件都不许添；原话里听不清、说乱了的地方，可以写成"她说得含糊"或者照她说的写。她前后说法不一致（比如年份），照实写"她有时说……有时说……"，不要替她裁决。
- 把零散的原话组织成有顺序、有呼吸的叙述，可以引用她的原话（用引号，照抄，错字可以改回通顺的字，但意思和用词不变）。
- 分成 {n_para} 段左右，每段 60-160 字。每段必须注明依据：refs 里列出这一段用到的原话编号（如 "E3-2" 表示第 3 条第 2 句）。没有依据的段落不要写。
- 给这一章起一个书的章名（4-10 个字，像书名，不要用"关于""的故事"），放在 title。
- 最后列出 2-3 个"线头"（threads）：她提到了但没展开、你作为读者最想听下去的具体小事；每个线头给一句儿子问她的话（大白话，一件小事，一句话能答）。

严格输出 JSON：
{{"title": "章名", "paragraphs": [{{"text": "……", "refs": ["E1-1", "E1-3"]}}], "threads": [{{"text": "线头是什么", "question": "怎么问她"}}]}}

材料：
{material}
"""


def _load_book():
    if os.path.exists(BOOK_PATH):
        try:
            with open(BOOK_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"title": TITLE, "subtitle": SUBTITLE, "chapters": [], "generated_at": None}


def _save_book(b):
    tmp = BOOK_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BOOK_PATH)


def _material(entries):
    lines = []
    for i, e in enumerate(entries, 1):
        lines.append(f"[E{i}]（{e.get('date', '')}）问：{e.get('question', '')}")
        for j, q in enumerate(e.get("quotes", []), 1):
            lines.append(f"  E{i}-{j}：{q}")
        if e.get("summary"):
            lines.append(f"  概括：{e['summary']}")
        for v in e.get("verifications", []):
            lines.append(f"  核实（{v.get('date', '')}）她说「{v.get('verdict', '')}」" + ("；更正：" + " / ".join(v.get("corrections", [])) if v.get("corrections") else ""))
    return "\n".join(lines)


def _hash(entries):
    return hashlib.md5(json.dumps(entries, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def render_chapter(chapter, entries):
    n_para = max(3, min(12, 2 + len(entries)))
    prompt = CHAPTER_PROMPT.format(elder_name=family.ELDER_NAME, elder_brief=family.ELDER_BRIEF, elder_birth=family.ELDER_BIRTH,
                                   book_title=family.BOOK_TITLE, child=family.CHILD_NAME, chapter=chapter, n_para=n_para, material=_material(entries)[-24000:])
    raw = deepseek_client._chat([{"role": "user", "content": prompt}], temperature=0.5).strip()
    if raw.startswith("```"):
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    data = json.loads(raw)
    # refs → {id, q}
    paras = []
    for p in data.get("paragraphs", []):
        refs = []
        for r in p.get("refs", []):
            try:
                ei, qi = str(r).lstrip("E").split("-")
                e = entries[int(ei) - 1]
                qi = int(qi)
                if 1 <= qi <= len(e.get("quotes", [])):
                    refs.append({"id": e["id"], "q": qi - 1})
            except Exception:
                continue
        if p.get("text"):
            paras.append({"text": p["text"].strip(), "refs": refs})
    return {"chapter": chapter, "title": data.get("title") or chapter, "paragraphs": paras,
            "threads": [t for t in data.get("threads", []) if t.get("question")][:3]}


def render(force=False):
    """渲染有变化的章节；返回重写了几章。"""
    book = _load_book()
    old = {c["chapter"]: c for c in book.get("chapters", [])}
    ents = story.load_entries()
    by_ch = {}
    for e in ents:
        by_ch.setdefault(e.get("chapter"), []).append(e)
    chapters, changed = [], 0
    for ch in story.CHAPTERS:
        es = by_ch.get(ch)
        if not es:
            continue
        h = _hash(es)
        prev = old.get(ch)
        if prev and prev.get("hash") == h and not force:
            chapters.append(prev)
            continue
        try:
            c = render_chapter(ch, es)
        except Exception:
            traceback.print_exc()
            if prev:
                chapters.append(prev)
            continue
        c["hash"] = h
        c["rendered_at"] = time.strftime("%Y-%m-%d %H:%M")
        # 线头进题库（以后 AI 会去问）
        for t in c.get("threads", []):
            q = story.add_question(t["question"], ch, pin=False)
            if q:
                story.set_question(q["id"], chapter=ch)
                t["qid"] = q["id"]
            else:
                # 已存在：找回 id
                for x in story._load_q():
                    if x["text"] == t["question"]:
                        t["qid"] = x["id"]
        chapters.append(c)
        changed += 1
    book.update(title=TITLE, subtitle=SUBTITLE, chapters=chapters, generated_at=time.strftime("%Y-%m-%d %H:%M"),
                entries=len(ents), quotes=sum(len(e.get("quotes", [])) for e in ents))
    _save_book(book)
    return changed


def payload():
    """给前端：书 + 被引用到的原话（含录音路径）"""
    book = _load_book()
    ents = {e["id"]: {"question": e.get("question"), "date": e.get("date"), "quotes": e.get("quotes", []), "audio": e.get("audio", [])}
            for e in story.load_entries()}
    return {"book": book, "entries": ents}


if __name__ == "__main__":
    import sys
    print("rendered chapters:", render(force="--force" in sys.argv))
