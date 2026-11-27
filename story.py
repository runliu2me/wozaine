#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""妈妈的故事（照 StoryWorth 的思路做的口述传记）。

StoryWorth 的核心做法：每周给父母一个问题 → 父母用自己的话回答（电话录音会原样转文字，不润色）
→ 一年攒成一本书；家人可以自己出题；问过的不重复；可以换题、跳过。
我们的版本：妈妈每天和 AI 聊好几次，每次对话里 AI 挑合适的时机问**一个**故事问题，
她答得短就鼓励多说、顺着她的话追问；每晚把当天她说过的故事**原话**摘出来存档（错字也留着），
再整理成按人生章节排的传记。家人在管理台可以出题、指定下次问哪个、看传记、导出。

文件（都在 DATA_DIR/story/）：
  questions.json   题库 + 每题状态（offered_at / asked_count / answered / parent / source）
  entries.jsonl    每条故事：日期、问题、妈妈原话（逐句，不改）、一句话概括、章节
  biography.md     自动整理的传记（原话用引用块原样保留，整理文字单独标出）
"""
import json
import os
import random
import threading
import time
import traceback

import deepseek_client
import family
from platform_compat import file_lock  # 跨进程文件锁：错字本回改故事条目时不和这里抢着写

DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
STORY_DIR = os.path.join(DATA_DIR, "story")
Q_PATH = os.path.join(STORY_DIR, "questions.json")
E_PATH = os.path.join(STORY_DIR, "entries.jsonl")
BIO_PATH = os.path.join(STORY_DIR, "biography.md")
_LOCK = threading.Lock()

# 章节和种子题库在 story_seed.json（按你家的情况改）。一次只问一件具体的事，大白话。
SEED_PATH = os.environ.get("COMPANION_STORY_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "story_seed.json"))
with open(SEED_PATH, "r", encoding="utf-8") as _f:
    SEED = json.load(_f)["chapters"]
CHAPTERS = list(SEED.keys())


def _ensure():
    os.makedirs(STORY_DIR, exist_ok=True)
    if not os.path.exists(Q_PATH):
        qs = []
        for ch in CHAPTERS:
            for t in SEED.get(ch, []):
                qs.append({"id": f"q{len(qs) + 1:03d}", "chapter": ch, "text": t, "source": "seed",
                           "offered_at": None, "asked_count": 0, "answered": False, "shelved": False, "parent": None})
        _save_q(qs)


def _load_q():
    _ensure()
    with open(Q_PATH, "r", encoding="utf-8") as f:
        qs = json.load(f)
    # 题库升级：种子里新加的题自动补进去（不动已有状态）
    have = {q["text"] for q in qs}
    added = False
    for ch in CHAPTERS:
        for t in SEED.get(ch, []):
            if t not in have:
                qs.append({"id": f"q{len(qs) + 1:03d}", "chapter": ch, "text": t, "source": "seed",
                           "offered_at": None, "asked_count": 0, "answered": False, "shelved": False, "parent": None})
                have.add(t); added = True
    if added:
        _save_q(qs)
    return qs


def _save_q(qs):
    tmp = Q_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(qs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, Q_PATH)


def _entry_id(e):
    import hashlib
    return hashlib.md5((e.get("date", "") + "|" + e.get("question", "")).encode("utf-8")).hexdigest()[:10]


def save_entries(ents):
    tmp = E_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in ents:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, E_PATH)


def load_entries():
    _ensure()
    out = []
    if os.path.exists(E_PATH):
        with open(E_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        e = json.loads(line)
                        e.setdefault("id", _entry_id(e))
                        out.append(e)
                    except Exception:
                        pass
    return out


# ---------------- 选题 ----------------
def pick_question(today=None):
    """"提问与补全"模式选题。目标是把整个题库（十几个章节）都拼满，不盯着一个话题问：
    家人指定的 > 覆盖率最低的章节里没问过的题（且不连续两次同一章）> 追问（最多三次里一次）。
    同一题 3 天内不重复给；问了 3 次还没答到就先搁置。"""
    today = today or time.strftime("%Y-%m-%d")
    with _LOCK:
        qs = _load_q()
        cand = [q for q in qs if not q["answered"] and not q["shelved"]]
        if not cand:
            return None
        pinned = [q for q in cand if q.get("pinned")]
        recent_offers = [q for q in qs if q.get("offered_at") and q["offered_at"][:10] >= _days_ago(2, today)]
        recent_chapters = [q["chapter"] for q in sorted(recent_offers, key=lambda x: x["offered_at"])[-2:]]
        n_recent = len(recent_offers)
        follow = [q for q in cand if q.get("parent") and q["asked_count"] == 0]
        fresh = [q for q in cand if not q.get("parent") and (q["offered_at"] is None or q["offered_at"][:10] < _days_ago(3, today))]
        if pinned:
            pool = pinned
        elif follow and n_recent % 3 == 2:          # 每三次给一次追问，其余时间往全局补
            pool = follow
        else:
            # 覆盖率最低的章节优先（已回答 / 总题数），跳过最近两次刚问过的章节
            cov = {}
            for ch in CHAPTERS:
                cq = [q for q in qs if q["chapter"] == ch]
                cov[ch] = (sum(1 for q in cq if q["answered"]) / len(cq)) if cq else 1.0
            chapters = sorted({q["chapter"] for q in fresh}, key=lambda c: (cov[c], random.random()))
            chapters = [c for c in chapters if c not in recent_chapters] or chapters
            pool = [q for q in fresh if q["chapter"] == chapters[0]] if chapters else (follow or cand)
            never = [q for q in pool if q["asked_count"] == 0]
            pool = never or pool
        q = random.choice(pool)
        q["offered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        q["asked_count"] += 1
        q["pinned"] = False
        if q["asked_count"] >= 3 and not q["answered"]:
            q["shelved"] = True
        _save_q(qs)
        return dict(q)


# ---------------- "核实与反馈"模式：把整理好的故事讲给她听，请她认一认 ----------------
def pick_retell(today=None):
    """挑一段值得核实的故事：原话够多（≥3 句）、还没核实过或 14 天前核实的、不是今天新讲的。
    第一次挑到时用 DeepSeek 把它整理成 AI 讲给妈妈听的一小段话（只用她说过的内容，不添加），存回条目。"""
    today = today or time.strftime("%Y-%m-%d")
    ents = load_entries()
    cand = [e for e in ents if len(e.get("quotes", [])) >= 3 and e.get("date", "") < today
            and (not e.get("verified_at") or e["verified_at"] < _days_ago(14, today))]
    if not cand:
        return None
    # 先核实内容最扎实的（原话多的）；核实过的排后面
    cand.sort(key=lambda e: (e.get("verified_at") or "", -len(e.get("quotes", [])), e.get("date", "")))
    e = cand[0]
    if not e.get("retell"):
        head = family.adapt_prompt(
            "你是{child}，下面是你妈妈前几天跟你讲的一段往事（她的原话和概括）。请把要跟她核对的内容拆成 **3 到 6 个小点**，"
            "每个点一句话、不超过 20 个字、只讲一件事，第二人称（\"你那时候……\"），大白话，家里人说话的口气，句尾带\"对吧？\"或\"是不是？\"。"
            "只用她说过的内容，一个细节都不要添加；地名人名年份照她说的。按事情发生的顺序排。"
            "严格输出 JSON：{{\"points\": [\"...\", \"...\"]}}\n\n"
            "她回答的问题："
        ).format(child=family.CHILD_NAME)
        prompt = f"{head}{e.get('question', '')}\n概括：{e.get('summary', '')}\n原话：\n" + "\n".join(e.get("quotes", []))
        try:
            raw = deepseek_client._chat([{"role": "user", "content": prompt}], temperature=0.3).strip()
            if raw.startswith("```"):
                raw = raw[raw.find("{"):raw.rfind("}") + 1]
            pts = [str(x).strip() for x in json.loads(raw).get("points", []) if str(x).strip()][:6]
            if not pts:
                return None
            e["retell"] = pts
        except Exception:
            traceback.print_exc()
            return None
        with _LOCK:
            all_e = load_entries()
            for x in all_e:
                if x["id"] == e["id"]:
                    x["retell"] = e["retell"]
            save_entries(all_e)
    with _LOCK:
        all_e = load_entries()
        for x in all_e:
            if x["id"] == e["id"]:
                x["retell_offered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_entries(all_e)
    return e


def retell_block(e, turn=1):
    """"核实与反馈"那场对话的提示。一轮只核对一个点：讲一句、等她回、再讲下一句。她记不住一长串。"""
    if not e:
        return ""
    pts = e.get("retell") or []
    if isinstance(pts, str):
        pts = [pts]
    k = max(0, turn - 1)
    if k < len(pts):
        if k == 0:
            step = (
                family.adapt_prompt(
                    "\n**这次是你主动喊的她，她刚应了一声。你这一句这么说：先\"哎，在啊\"接住她，再说\"前两天你跟我讲了{chapter}的事，我理了理，我说你听听\"，然后只说第 1 点：**\n"
                ).format(chapter=e.get("chapter", "以前"))
                + f"「{pts[0]}」\n"
                + family.adapt_prompt("**说完就停，等她回。后面的点一轮说一个，不要一口气全说。**")
            )
        else:
            step = (
                family.adapt_prompt(
                    f"\n**这是第 {k + 1} 轮。先用一句话接住她上一句（她说对就应一声；她说不对就问\"哪儿不对？\"并用她的话复述确认），然后只核对下面这一点：**\n"
                )
                + f"「{pts[k]}」\n"
                + family.adapt_prompt(f"**说完就停，等她回。**（一共 {len(pts)} 点，已经核到第 {k + 1} 点）")
            )
    else:
        step = family.adapt_prompt(
            f"\n**所有 {len(pts)} 点都核完了（或她已经不耐烦）。这一轮：接住她上一句，真心说一句听了的感受（一句就好），然后聊回眼前的事。不要再核对了。**"
        )
    header = family.adapt_prompt("## 这次聊天是\"核实\"：把你整理过的她的故事一句一句讲给她认（每轮只说一个点）")
    rules = family.adapt_prompt("""规矩：
- 每轮只说一两句，说完就停。她年纪大了，一长串她记不住。
- 她说"不对"：问"哪儿不对？"，听完用她的话重复一遍确认，别争、别解释你为什么记错。
- 她顺着讲出了新东西：那比核对重要，先听她讲，追一小步就够；这一轮的点可以放到下一轮再核。
- 她不想说、或者"记不得了"：就说"没事，我记着呢"，直接收尾聊回眼前的事。
- 绝不说"记录""传记""采访"。
""")
    return f"\n{header}\n{step}\n{rules}"


def _days_ago(n, today):
    t = time.strptime(today, "%Y-%m-%d")
    return time.strftime("%Y-%m-%d", time.localtime(time.mktime(t) - n * 86400))


def prompt_block(q, proactive=False, turn=1):
    """拼进 system prompt 的一段：这场对话里怎么问故事。针对的是"话很短、不太愿意多说"的老人：
    不指望她一口气讲长的，一次只要一个小细节，一句也算收获，靠很多次慢慢攒。"""
    if not q:
        return ""
    opening = ""
    if proactive and turn == 1:
        opening = family.adapt_prompt("""
**这次是你主动喊的她（"妈，你在吗？"），她刚应了一声。你这一句就要这么说：先"哎，在啊"接住她，然后直接说"有件事想问你"，接着把下面这个问题问出来。
一句话里把这三步做完，不要再闲聊铺垫，不要问"吃了没"。例如："哎，在啊。妈，有件事想问你——你小时候家里的灶台是搭在哪儿的？"**
""")
    title = family.adapt_prompt("## 这次聊天里想听妈妈讲的一个故事（每次只问这一个，别贪多）")
    body = family.adapt_prompt("""妈妈话少、每句都短，这很正常，**一句就是收获**，不要指望她一口气讲长的。照下面做：
1. **先铺垫再问**（主动喊她的那种除外，上面已经说了直接问）：打过招呼、接住她眼前的事之后，先自己说一两句相关的小事（"我今天路过一个菜场，想起你以前……"），再把问题带出来。自己先给，她才肯给。
2. **只要一个小细节**：不问"讲讲你小时候"，问"你小时候睡的床是木头的还是竹子的？"——给她选项，让她只用说一个词就能接上。
3. **她答一句，你就接住那一句再往前挪一小步**：把她的话重复半句（"哦，木头的床……"），然后只追一个小问题："几个人睡？""冬天冷不冷？""谁给你盖被子？"。一次一个，不连珠炮。
4. **多用能感觉到的东西**：味道、声音、冷热、颜色、谁在旁边。"那时候下雪冷不冷？""灶上烧的什么味儿？"比"你有什么感想"好问得多。
5. **顺着她的话走，不拉回来**：她岔到别的旧事上，那就是故事，跟着走；她记错了、前后不一样，不纠正、不点破。
6. **她说"不记得了""没什么"，就换个更小的问法**问一次（"那你记不记得那时候家门口有没有树？"），还是不说就放下，聊回眼前的事，这题下次再问。
7. **每次聊两三个来回就够**，不要把一次聊天都变成问过去。她说完，真心说一句你听了的感受（一句就好，别肉麻），然后回到平常的聊天。
8. 绝不说"我在记录""这是传记""采访"这类话，就是儿子想听妈妈讲以前的事。
""")
    return f"\n{title}\n问题：**{q['text']}**（章节：{q['chapter']}）\n{opening}\n{body}"


def known_block(max_chars=30000):
    """每一轮都拼进 system prompt：她已经讲过的人生故事（每条：当时的问题 + 概括 + 原话里的关键细节）和已经问过的题。
    DeepSeek 上下文大、便宜，所以这里放得很全——目的是往细里挖的时候，模型知道哪些细节已经问过、她怎么说的。"""
    ents = load_entries()
    qs = _load_q()
    asked = [q for q in qs if q["asked_count"] > 0 and not q["answered"]]
    if not ents and not asked:
        return ""
    lines = [family.adapt_prompt("## 妈妈已经跟你讲过的人生故事（她自己说的，可靠。平常聊天可以自然提起；别重复问已经讲过的，要问就顺着往更细的地方问）")]
    by_ch = {}
    for e in ents:
        by_ch.setdefault(e.get("chapter", ""), []).append(e)
    for ch in CHAPTERS:
        if ch not in by_ch:
            continue
        lines.append(f"### {ch}")
        for e in by_ch[ch]:
            lines.append(f"- 【{e.get('date', '')}】问：{e.get('question', '')}")
            if e.get("summary"):
                lines.append(f"  概括：{e['summary']}")
            quotes = [x for x in e.get("quotes", []) if len(x) >= 4]
            if quotes:
                lines.append(f"  {family.ELDER_PRONOUN}的原话：" + " / ".join(quotes[:12]))
    if asked:
        lines.append(f"### 问过但{family.ELDER_PRONOUN}还没讲到的题（下次可以换个更小的问法再试，别原样重复）")
        for q in asked[-30:]:
            lines.append(f"- {q['text']}（问过 {q['asked_count']} 次）")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return "\n" + text + "\n"


# ---------------- 每晚：从当天对话里摘故事 ----------------
EXTRACT_PROMPT = """下面是妈妈（{elder_name}，{elder_brief}）今天和儿子"{child}"的对话记录（妈妈那边是语音识别出来的文字，可能有错字、断句错）。

任务：把妈妈**讲的过去的事**（她的经历、家里人、以前的生活、她的看法和心愿）摘出来，整理成故事条目。只要她自己说的，不要儿子说的；今天的日常（吃了什么、天气、练了没）不算故事，除非她顺带讲到了过去。

每个条目：
- chapter：从这个列表里选一个：{chapters}
- question：这段故事回答的是什么问题，用儿子问妈妈的口气写成一句话（例如"你第一份工作是在哪儿？干什么活？"）；如果就是今天问的问题，照抄原题
- quotes：妈妈的**原话**，逐句照抄，一个字都不要改（错字也留着），按说的顺序，去掉和这件事无关的句子
- summary：用第三人称一两句话概括她说了什么（可以把错字改回来、把断句理顺，但不要加她没说的内容）
- follow_ups：根据她说的内容，最想接着问她的 1-2 个具体小问题（大白话，一次只问一件事）

今天问的问题：{todays_q}
{retold}
严格只输出 JSON：{{"entries": [...], "verification": {{"verdict": "对/部分不对/不对/没核实", "corrections": ["她更正或补充的原话，逐句照抄"]}}}}；她今天没讲过去的事就输出 {{"entries": [], "verification": null}}。

对话记录：
{dialog}
"""


def extract_day(date, turns, todays_q=None, retold_id=None):
    """返回新增条目数。会把 follow_ups 加进题库（带 parent），并把今天的问题标成已回答。"""
    _ensure()
    mom = [t for t in turns if t.get("role") == "user"]
    if len(mom) < 2:
        return 0
    lines = [(f"{family.ELDER_LABEL}：" if t["role"] == "user" else f"{family.CHILD_NAME}：") + t["text"].replace("[电话] ", "") for t in turns]
    # 聊得多的日子一天几百句，分成约 9000 字一段逐段摘，别只看结尾
    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) > 9000 and cur:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        chunks.append(cur)
    entries = []
    for dialog in chunks:
        retold_txt = ""
        retold = next((x for x in load_entries() if x["id"] == retold_id), None) if retold_id else None
        if retold:
            retold_txt = (
                f"\n今天 AI 把这段故事复述给{family.ELDER_PRONOUN}核实了：{retold.get('summary', '')}。"
                f"请判断{family.ELDER_PRONOUN}的态度（对/部分不对/不对），把{family.ELDER_PRONOUN}更正或补充的原话记进 verification.corrections。\n"
            )
        prompt = family.adapt_prompt(EXTRACT_PROMPT).format(
            elder_name=family.ELDER_NAME, elder_brief=family.ELDER_BRIEF, child=family.CHILD_NAME,
            chapters="、".join(CHAPTERS), todays_q=(todays_q or "（无）"), dialog=dialog, retold=retold_txt,
        )
        try:
            import typo_book
            hint = typo_book.prompt_hint()
            if hint:
                prompt += "\n\n" + hint + "\n（quotes 照抄对话记录里的文字，不要自己改字；summary 里按正确的词写。）\n"
        except Exception:
            pass
        try:
            raw = deepseek_client._chat([{"role": "user", "content": prompt}], temperature=0.1).strip()
            if raw.startswith("```"):
                raw = raw[raw.find("{"):raw.rfind("}") + 1]
            data = json.loads(raw)
        except Exception:
            traceback.print_exc()
            continue
        entries += [e for e in data.get("entries", []) if e.get("quotes") and "这段最像" not in str(e.get("question", ""))]
        v = data.get("verification")
        if retold and isinstance(v, dict) and v.get("verdict") and v["verdict"] != "没核实":
            with _LOCK, file_lock(E_PATH):
                all_e = load_entries()
                for x in all_e:
                    if x["id"] == retold["id"]:
                        x["verified_at"] = date
                        x.setdefault("verifications", []).append({"date": date, "verdict": v["verdict"], "corrections": v.get("corrections") or []})
                        x["retell"] = None   # 有更正就下次重新整理
                save_entries(all_e)
            if v.get("corrections"):
                entries.append({"chapter": retold.get("chapter"), "question": f"（核实时{family.ELDER_PRONOUN}更正/补充）" + retold.get("question", ""),
                                "quotes": v["corrections"], "summary": f"核实时{family.ELDER_PRONOUN}说：{v['verdict']}。", "follow_ups": []})
    if not entries:
        return 0
    # 每句原话对应的录音：按文字在当天的对话里找到那一句，把它的录音路径（data/ 下）记进条目。录音文件永远保留。
    mom_turns = [t for t in turns if t.get("role") == "user" and t.get("audio")]
    raw_of = {t["text"].strip(): t["asr_raw"] for t in turns if t.get("role") == "user" and t.get("asr_raw")}
    def _audio_for(quote):
        q = quote.strip()
        for t in mom_turns:
            if t["text"].strip() == q:
                return t["audio"]
        for t in mom_turns:  # 识别文本被截了一截也能对上
            if q and (q in t["text"] or t["text"].strip() in q) and len(q) >= 6:
                return t["audio"]
        return None
    with _LOCK, file_lock(E_PATH):
        qs = _load_q()
        by_text = {q["text"]: q for q in qs}
        with open(E_PATH, "a", encoding="utf-8") as f:
            for e in entries:
                e["date"] = date
                e["audio"] = [_audio_for(x) for x in e.get("quotes", [])]
                # 错字本：原话显示纠正后的字，识别原文另存 quotes_raw 可查
                # 对话记录喂给模型的已经是纠正后的文字，这里按原句找回识别原文
                try:
                    import typo_book
                    fixed = [typo_book.correct_text(x) for x in e.get("quotes", [])]
                    raws = [raw_of.get(x.strip(), x) for x in e.get("quotes", [])]
                    e["quotes"] = fixed
                    if raws != fixed:
                        e["quotes_raw"] = raws
                except Exception:
                    traceback.print_exc()
                e["chapter"] = e.get("chapter") if e.get("chapter") in CHAPTERS else CHAPTERS[0]
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                # 标记题目已回答
                if todays_q and e.get("question") and (e["question"] == todays_q or todays_q[:6] in e["question"]):
                    q = by_text.get(todays_q)
                    if q:
                        q["answered"] = True
                        q["answered_at"] = date
                # 追问入库（每天最多 2 条，去重）
                for fu in (e.get("follow_ups") or [])[:2]:
                    fu = fu.strip()
                    if fu and fu not in by_text:
                        nq = {"id": f"q{len(qs) + 1:03d}", "chapter": e["chapter"], "text": fu, "source": "follow_up",
                              "offered_at": None, "asked_count": 0, "answered": False, "shelved": False,
                              "parent": e.get("question"), "created": date}
                        qs.append(nq)
                        by_text[fu] = nq
        _save_q(qs)
    return len(entries)


# ---------------- 每晚：根据她已经讲过的，生出"只有她能答"的小问题 ----------------
def generate_personal_questions(facts, max_new=3):
    """StoryWorth 的"Magic Questions"：按她本人讲过的事出题（比如她提过当兵、学手艺、老同学），
    比通用题更容易接上话。每晚最多加 3 道，去重。"""
    ents = load_entries()[-12:]
    if not ents and not facts:
        return 0
    known = "\n".join("- " + e.get("summary", "") for e in ents if e.get("summary"))
    known += "\n" + "\n".join("- " + (f["text"] if isinstance(f, dict) else str(f)) for f in facts[:40])
    qs_all = _load_q()
    existing = "\n".join("- " + q["text"] for q in qs_all[-80:])
    head = family.adapt_prompt(
        "下面是妈妈（{elder_brief}）已经讲过的事和我们知道的情况。请根据这些，出 {max_new} "
        "个儿子想继续问她的小问题：每个只问一件具体的小事（人、地方、味道、当时的情形），大白话，"
        "一句话能答上的那种，不要问感想和道理，不要和已有的题重复。\n\n她讲过的事：\n"
    ).format(elder_brief=family.ELDER_BRIEF, max_new=max_new)
    prompt = (head + known +
              "\n\n已有的题（别重复）：\n" + existing + "\n\n严格输出 JSON：{\"questions\": [{\"chapter\": \"从" + "、".join(CHAPTERS) +
              "里选\", \"text\": \"...\"}]}")
    try:
        raw = deepseek_client._chat([{"role": "user", "content": prompt}], temperature=0.5).strip()
        if raw.startswith("```"):
            raw = raw[raw.find("{"):raw.rfind("}") + 1]
        items = json.loads(raw).get("questions", [])
    except Exception:
        traceback.print_exc()
        return 0
    n = 0
    for it in items[:max_new]:
        t = str(it.get("text", "")).strip()
        if t and add_question(t, it.get("chapter"), pin=False):
            # 标成 follow_up 来源，排在新题前面
            with _LOCK:
                qs = _load_q()
                for q in qs:
                    if q["text"] == t:
                        q["source"] = "follow_up"; q["parent"] = "personal"
                _save_q(qs)
            n += 1
    return n


# ---------------- 整理成传记 ----------------
def compile_biography():
    ents = load_entries()
    lines = [
        f"# {family.ELDER_LABEL}的故事",
        "",
        f"_由“我在呢”从{family.ELDER_LABEL}平时和 AI 的聊天里一点点攒出来的。引用块里是{family.ELDER_LABEL}的原话（语音识别的，只按错字本改了听错的词，识别原文另存可查）；其他是整理。更新于 {time.strftime('%Y-%m-%d')}。_",
        "",
    ]
    total = 0
    for ch in CHAPTERS:
        items = [e for e in ents if e.get("chapter") == ch]
        if not items:
            continue
        lines.append(f"## {ch}")
        lines.append("")
        for e in items:
            total += 1
            lines.append(f"**{e.get('question', '')}**  ·  {e.get('date', '')}")
            lines.append("")
            audios = e.get("audio") or []
            for i, qline in enumerate(e.get("quotes", [])):
                a = audios[i] if i < len(audios) else None
                lines.append(f"> {qline}" + (f"  〔录音：{a}〕" if a else ""))
            lines.append("")
            if e.get("summary"):
                lines.append(f"_{e['summary']}_")
                lines.append("")
            for v in e.get("verifications", []):
                lines.append(f"_核实（{v['date']}）：{family.ELDER_PRONOUN}说「{v['verdict']}」_")
                lines.append("")
    if total == 0:
        lines.append("_还没有故事。AI 会在聊天里慢慢问。_")
    text = "\n".join(lines)
    with _LOCK, file_lock(BIO_PATH):
        orig_mode = None
        try:
            orig_mode = os.stat(BIO_PATH).st_mode & 0o777
        except OSError:
            pass
        tmp = f"{BIO_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
        if orig_mode is not None:
            try:
                os.chmod(tmp, orig_mode)
            except OSError:
                pass
        os.replace(tmp, BIO_PATH)
    return text


def status():
    qs = _load_q()
    ents = load_entries()
    by_ch = {}
    for ch in CHAPTERS:
        cq = [q for q in qs if q["chapter"] == ch]
        by_ch[ch] = {"total": len(cq), "answered": sum(1 for q in cq if q["answered"]),
                     "entries": sum(1 for e in ents if e.get("chapter") == ch)}
    bio = open(BIO_PATH, "r", encoding="utf-8").read() if os.path.exists(BIO_PATH) else ""
    return {"chapters": by_ch, "questions": qs, "entries": len(ents), "entry_list": ents, "biography": bio,
            "last_entry_date": ents[-1]["date"] if ents else None}


def add_question(text, chapter=None, pin=False):
    with _LOCK:
        qs = _load_q()
        if any(q["text"] == text for q in qs):
            return None
        nq = {"id": f"q{len(qs) + 1:03d}", "chapter": chapter if chapter in CHAPTERS else CHAPTERS[-1], "text": text,
              "source": "family", "offered_at": None, "asked_count": 0, "answered": False, "shelved": False,
              "parent": None, "pinned": bool(pin), "created": time.strftime("%Y-%m-%d")}
        qs.append(nq)
        _save_q(qs)
        return nq


def set_question(qid, **fields):
    with _LOCK:
        qs = _load_q()
        for q in qs:
            if q["id"] == qid:
                if fields.get("pinned"):
                    for o in qs:
                        o["pinned"] = False
                q.update({k: v for k, v in fields.items() if k in ("pinned", "shelved", "answered", "text", "chapter")})
                _save_q(qs)
                return q
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "compile":
        print(compile_biography())
    else:
        print(json.dumps(pick_question(), ensure_ascii=False))
