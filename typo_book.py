#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错字本：妈妈的话被语音识别听错的词，确认一次，以后和以前的内容都按它纠正。

规矩：
  - 只有管理员能确认词（管理台「记忆 → 错字本」，家人账号看不到）；
  - 读音相近的命中直接改，但每一处都能撤销；
  - 识别的原文永远留着：对话记录里每句另存 asr_raw，故事原话另存 quotes_raw，页面上能看「原识别」；
  - 第一批词可以从历史对话里统计出来，人工挑过后写进 typo_seed.json（见 SEED_TERMS）。

每个词（term）有三类写法：
  wrong     确认的错写：任何地方、任何时候直接替换（入库前、回改历史、派生内容）。
  maybe     本身是正常词，要看上下文（比如妈妈句尾喊孩子名字被听成「牛肉」「女人」，但「牛肉炒韭菜」是真牛肉）：
            每晚交给 DeepSeek 逐处判断，判成错写的才改，也记在那一句上、可撤销。
  rejected  撤销过的写法：不再改，读音查找也不再提。
读音查找：每晚按拼音（n/l、前后鼻音、平翘舌不分，照很多地方口音的习惯）在历史里找和正确词只差一个音的写法，
自动放进 maybe；DeepSeek 判过足够多次、几乎全是错写的升级成 wrong，几乎都不是的放进 rejected。

文件（都在 DATA_DIR）：
  typo_book.json   词表
  typo_state.json  DeepSeek 判过哪些地方（不重复判）、被单独撤销的那几处、上次运行情况
  typo_log.jsonl   派生内容（记忆、日报、故事概括、题目、书）每次修改前后的全文，撤销用
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback

import family
from platform_compat import file_lock

DATA_DIR = os.environ.get("COMPANION_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
BOOK_PATH = os.path.join(DATA_DIR, "typo_book.json")
STATE_PATH = os.path.join(DATA_DIR, "typo_state.json")
LOG_PATH = os.path.join(DATA_DIR, "typo_log.jsonl")
LOCK_PATH = os.path.join(DATA_DIR, "typo.lock")

# 第一批词（可选）：typo_seed.json，格式见 typo_seed.example.json。只在第一次建词表时读一次，
# 之后都在管理台「记忆 → 错字本」里增删。建议先跑一两周，从对话记录里统计出常被听错的人名地名再填。
_SEED_PATH = os.environ.get("COMPANION_TYPO_SEED", os.path.join(os.path.dirname(os.path.abspath(__file__)), "typo_seed.json"))
try:
    with open(_SEED_PATH, encoding="utf-8") as _f:
        SEED_TERMS = json.load(_f).get("terms", [])
except FileNotFoundError:
    SEED_TERMS = []

# DeepSeek 判断后自动升级/放弃的门槛
PROMOTE_MIN_YES = 8        # 判成错写至少这么多处
PROMOTE_RATIO = 0.9        # 且错写比例不低于这个 → 升级成 wrong（只对不是常用词的写法）
REJECT_MIN_JUDGED = 10     # 判过至少这么多处
REJECT_RATIO = 0.15        # 错写比例低于这个 → 放进 rejected
DISCOVER_MIN_COUNT = 3     # 读音查找：至少出现几次才提
JUDGE_BATCH = 25           # 一次问 DeepSeek 多少句
NIGHT_BUDGET = int(os.environ.get("TYPO_NIGHT_BUDGET", "800"))  # 每晚最多判多少句（陆陆续续地改）
# 判断用哪个模型（用真实句子试过）：
#   管理员确认的「看上下文」词（句尾喊人被听成「牛肉」「女人」这类）：deepseek-chat 判得准，一批 10 多秒；
#   读音查找自动找来的写法（比如「是谁」「你睡」像某个地名）：deepseek-chat 几乎全判成错写，不能用；
#   deepseek-reasoner 判得对（0/35 误判），但一批要一到五分钟，所以只用它试判自动找来的写法，每晚有上限。
JUDGE_MODEL = os.environ.get("TYPO_JUDGE_MODEL", "deepseek-chat")
AUTO_JUDGE_MODEL = os.environ.get("TYPO_AUTO_JUDGE_MODEL", "deepseek-reasoner")
AUTO_NIGHT_BUDGET = int(os.environ.get("TYPO_AUTO_NIGHT_BUDGET", "120"))  # 自动找来的写法每晚最多试判多少句

_TLOCK = threading.Lock()
_CACHE = {"mtime": None, "book": None, "compiled": None}
HAN = re.compile(r"[一-鿿]+")


# ---------------- 文件锁：见 platform_compat.file_lock（兼容 POSIX fcntl / Windows msvcrt） ----------------


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path, obj, indent=1):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


# ---------------- 词表 ----------------
def _seed_book():
    terms = []
    for i, t in enumerate(SEED_TERMS, 1):
        terms.append({"id": f"t{i}", "right": t["right"], "note": t.get("note", ""), "wrong": list(t.get("wrong", [])),
                      "maybe": list(t.get("maybe", [])), "rejected": [], "added_at": _now(), "source": "seed"})
    return {"terms": terms, "next_id": len(terms) + 1}


def load_book():
    with _TLOCK:
        try:
            mtime = os.path.getmtime(BOOK_PATH)
        except OSError:
            mtime = None
        if mtime is None:
            book = _seed_book()
            os.makedirs(DATA_DIR, exist_ok=True)
            with file_lock(BOOK_PATH):
                if not os.path.exists(BOOK_PATH):
                    _write_json(BOOK_PATH, book)
            mtime = os.path.getmtime(BOOK_PATH)
        if _CACHE["mtime"] != mtime:
            with open(BOOK_PATH, encoding="utf-8") as f:
                _CACHE["book"] = json.load(f)
            _CACHE["mtime"] = mtime
            _CACHE["compiled"] = None
        return _CACHE["book"]


def save_book(book):
    with file_lock(BOOK_PATH):
        _write_json(BOOK_PATH, book)
    with _TLOCK:
        _CACHE["mtime"] = None


def _term(book, tid):
    return next((t for t in book["terms"] if t["id"] == tid), None)


def _compiled():
    book = load_book()
    if _CACHE["compiled"] is None:
        rights = {t["right"] for t in book["terms"] if t.get("right")}
        wrong = {}
        for t in book["terms"]:
            for w in t.get("wrong", []):
                if w and w not in rights:
                    wrong[w] = (t["right"], t["id"])
        cands = sorted(rights | set(wrong), key=len, reverse=True)
        _CACHE["compiled"] = {"rights": rights, "wrong": wrong, "cands": cands,
                              "first": {c[0] for c in cands}}
    return _CACHE["compiled"]


# ---------------- 纠正：只做确认过的错写（wrong）----------------
def find_fixes(raw, veto=()):
    """在原文里找确认过的错写。返回 [{"s": 在原文里的位置, "from", "to", "t": 词 id}]。
    正确词本身会被保护：比如「王芳芳」里的「王芳」不会再被改一次。"""
    c = _compiled()
    out, i, n = [], 0, len(raw)
    while i < n:
        if raw[i] not in c["first"]:
            i += 1
            continue
        hit = None
        for cand in c["cands"]:
            if raw.startswith(cand, i):
                hit = cand
                break
        if hit is None:
            i += 1
            continue
        if hit in c["wrong"] and (i, hit) not in veto:
            right, tid = c["wrong"][hit]
            out.append({"s": i, "from": hit, "to": right, "t": tid})
        i += len(hit)
    return out


def render(raw, fixes):
    """按记录的修改把原文变成纠正后的文字（位置都是原文里的位置，互不重叠才用）。"""
    out, pos = [], 0
    for f in sorted(fixes, key=lambda x: x["s"]):
        s, frm = f["s"], f["from"]
        if s < pos or raw[s:s + len(frm)] != frm:
            continue
        out.append(raw[pos:s])
        out.append(f["to"])
        pos = s + len(frm)
    out.append(raw[pos:])
    return "".join(out)


def correct(text):
    """入库前用：返回 (纠正后的文字, 修改列表)。只改确认过的错写，不调用模型，几毫秒。"""
    if not text:
        return text, []
    try:
        fixes = find_fixes(text)
    except Exception:
        traceback.print_exc()
        return text, []
    return (render(text, fixes) if fixes else text), fixes


def correct_text(text):
    return correct(text)[0]


def _valid_llm_fix(book, f):
    t = _term(book, f.get("t"))
    return bool(t) and f.get("from") in t.get("maybe", []) and t.get("right") == f.get("to")


def refix(raw, old_fixes, veto=()):
    """重新算一句的修改：确认错写按当前词表重算；DeepSeek 判过的保留（词或写法被撤销了就去掉）。"""
    book = load_book()
    det = find_fixes(raw, veto)
    taken = [(f["s"], f["s"] + len(f["from"])) for f in det]
    keep = []
    for f in old_fixes or []:
        if f.get("by") != "llm" or (f["s"], f["from"]) in veto or not _valid_llm_fix(book, f):
            continue
        a, b = f["s"], f["s"] + len(f["from"])
        if raw[a:b] != f["from"] or any(a < y and x < b for x, y in taken):
            continue
        keep.append(f)
        taken.append((a, b))
    return sorted(det + keep, key=lambda x: x["s"])


def fix_item(item, raw_key, text_key, fixes_key, veto=()):
    """纠正一条记录（对话一句 / 故事里的一句原话）：原文放 raw_key，修改放 fixes_key。返回是否有变化。"""
    raw = item.get(raw_key) or item.get(text_key) or ""
    fixes = refix(raw, item.get(fixes_key), veto)
    new_text = render(raw, fixes) if fixes else raw
    changed = new_text != item.get(text_key) or (fixes or None) != (item.get(fixes_key) or None)
    item[text_key] = new_text
    if fixes:
        item[raw_key] = raw
        item[fixes_key] = fixes
    else:
        item.pop(raw_key, None)
        item.pop(fixes_key, None)
    return changed


# ---------------- 给识别模型和 DeepSeek 的提示 ----------------
def hotwords(limit=60):
    """所有正确词（给识别模型当热词），按词表顺序，最重要的放前面。"""
    try:
        book = load_book()
    except Exception:
        return []
    out = [t["right"] for t in book["terms"] if t.get("right")]
    return out[:limit]


def prompt_hint():
    """拼进 DeepSeek 提示词：哪些词常被听错。"""
    try:
        book = load_book()
    except Exception:
        return ""
    lines = []
    for t in book["terms"]:
        ws = t.get("wrong", [])[:6]
        ms = t.get("maybe", [])[:6]
        if not ws and not ms:
            continue
        s = f"- {t['right']}" + (f"（{t['note']}）" if t.get("note") else "") + "："
        if ws:
            s += "常被听成「" + "」「".join(ws) + "」"
        if ms:
            s += ("；" if ws else "") + "有时被听成「" + "」「".join(ms) + "」（要看上下文）"
        lines.append(s)
    if not lines:
        return ""
    return (f"## 语音识别的常见错字（错字本，家人确认过）\n{family.ELDER_LABEL}那边的文字是语音识别出来的。下面这些写法多半是识别错了，"
            f"理解{family.ELDER_PRONOUN}的意思时按正确的词理解，不要当成别的人或别的事：\n" + "\n".join(lines))


# ---------------- 读音 ----------------
_PY_CACHE = {}


def _syl(text):
    """每个字的读音，按口音模糊：n=l，zh/ch/sh=z/c/s，ng 尾=n 尾。"""
    from pypinyin import lazy_pinyin
    key = text
    if key in _PY_CACHE:
        return _PY_CACHE[key]
    out = []
    for p in lazy_pinyin(text, errors=lambda x: ["?"] * len(x)):
        ini = ""
        for i in ("zh", "ch", "sh"):
            if p.startswith(i):
                ini = i
                break
        if not ini and p and p[0] in "bpmfdtnlgkhjqxrzcsyw":
            ini = p[0]
        fin = p[len(ini):]
        ini = {"zh": "z", "ch": "c", "sh": "s", "n": "l"}.get(ini, ini)
        fin = re.sub(r"ng$", "n", fin)
        out.append((ini, fin))
    if len(_PY_CACHE) < 50000:
        _PY_CACHE[key] = out
    return out


_EX_CACHE = {}


def _exact_syl(text):
    if text not in _EX_CACHE:
        from pypinyin import lazy_pinyin
        if len(_EX_CACHE) > 200000:
            _EX_CACHE.clear()
        _EX_CACHE[text] = lazy_pinyin(text, errors=lambda x: ["?"] * len(x))
    return _EX_CACHE[text]


def _initial(p):
    for i in ("zh", "ch", "sh"):
        if p.startswith(i):
            return i
    return p[0] if p and p[0] in "bpmfdtnlgkhjqxrzcsyw" else ""


def _near(word_syl, cand_syl, word_exact, cand_exact):
    """读音相近：最多一个字不同音（声母真的一样，或韵母一样），其余字同音（n/l、前后鼻音、平翘舌算同音）。
    两个字的词更容易撞上别的词，所以只许一处「不一样」：模糊同音也算一处。"""
    d = fuzzy = 0
    for a, b, ea, eb in zip(word_syl, cand_syl, word_exact, cand_exact):
        if a == b:
            if ea != eb:
                fuzzy += 1
            continue
        if (a[0] == b[0] and _initial(ea) == _initial(eb)) or a[1] == b[1]:
            d += 1
        else:
            return None
    if d > 1:
        return None
    if len(word_syl) <= 2 and d + fuzzy > 1:
        return None
    if d + fuzzy > 2:
        return None
    return d + fuzzy


_JIEBA = {}


def _common_word(w):
    """是不是词典里的常用词（常用词不能无条件替换，要看上下文）。"""
    try:
        if "freq" not in _JIEBA:
            import jieba
            jieba.setLogLevel(60)
            jieba.initialize()
            _JIEBA["freq"] = jieba.dt.FREQ
        return _JIEBA["freq"].get(w, 0) >= 20
    except Exception:
        return True   # 判断不了就当常用词，走保守的「看上下文」


def discover(term, texts):
    """在 texts（原识别文字）里找读音和 term 正确词只差一个音的写法。返回 {写法: 次数}。"""
    right = term["right"]
    n = len(right)
    if n < 2:
        return {}
    ws = _syl(right)
    we = _exact_syl(right)
    book = load_book()
    known = set()
    for t in book["terms"]:      # 任何词已经登记过的写法都不再提
        known |= set(t.get("wrong", [])) | set(t.get("maybe", [])) | set(t.get("rejected", []))
    others = {t["right"] for t in book["terms"]}
    cnt = {}
    for text in texts:
        for seg in HAN.findall(text):
            if len(seg) < n:
                continue
            ss = _syl(seg)
            if len(ss) != len(seg):
                continue
            for k in range(len(seg) - n + 1):
                cand = seg[k:k + n]
                if cand == right or cand in known or cand in others:
                    continue
                if len(set(cand)) == 1:          # 「刘刘」这种结巴重复
                    continue
                if any(cand in o or o in cand for o in others):  # 和别的正确词重叠（比如「小小明」里的「小小」）
                    continue
                if _near(ws, ss[k:k + n], we, _exact_syl(cand)) is not None:
                    cnt[cand] = cnt.get(cand, 0) + 1
    return {c: v for c, v in cnt.items() if v >= DISCOVER_MIN_COUNT}


# ---------------- 状态 ----------------
def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("judged", {})     # "词id|写法" -> {"yes": n, "no": n, "keys": {句子key: [判成错写的位置...]}}
    st.setdefault("veto", {})       # 句子key -> [[位置, 写法], ...]  单独撤销的那几处
    st.setdefault("runs", [])
    return st


def _save_state(st):
    st["runs"] = st["runs"][-30:]
    _write_json(STATE_PATH, st, indent=None)


def line_key(item):
    """对话里一句话的稳定标识：时间 + 录音 + 原文哈希。"""
    raw = item.get("asr_raw") or item.get("text") or ""
    return hashlib.md5(f"{item.get('ts', '')}|{item.get('audio', '')}|{raw}".encode("utf-8")).hexdigest()[:12]


def _veto_of(st, key):
    return {(int(s), w) for s, w in st["veto"].get(key, [])}


# ---------------- 派生内容的修改记录（撤销用）----------------
def _log(kind, before, after):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), "kind": kind, "before": before, "after": after}, ensure_ascii=False) + "\n")


def _fix_derived(text):
    """派生内容（模型写的概括、记忆、日报、书）只改确认过的错写，没有原文可留，改前改后记进 typo_log。"""
    if not text or not isinstance(text, str):
        return text
    new, fixes = correct(text)
    return new


# ---------------- 回改历史 ----------------
def apply_history(st):
    """对话记录：妈妈的每句话按当前词表重算。返回改动句数。"""
    import memory_store
    changed = 0

    def _fn(items):
        nonlocal changed
        for it in items:
            if it.get("role") != "user":
                continue
            key = line_key(it)
            if fix_item(it, "asr_raw", "text", "fixes", _veto_of(st, key)):
                changed += 1
        return changed > 0

    memory_store.rewrite_history(_fn)
    return changed


def _history_raw_index():
    """原识别文字 → 纠正后的文字和修改（给故事原话复用 DeepSeek 在对话记录里判过的结果）。"""
    import memory_store
    idx = {}
    for it in memory_store.iter_history_raw():
        if it.get("role") == "user" and it.get("fixes"):
            idx[it.get("asr_raw") or it["text"]] = it["fixes"]
    return idx


def apply_story():
    """故事条目：原话逐句按当前词表重算（原话原文放 quotes_raw）；概括、问题等改确认过的错写。"""
    import story
    hidx = _history_raw_index()
    n = 0
    with file_lock(story.E_PATH):
        ents = story.load_entries()
        for e in ents:
            quotes = e.get("quotes") or []
            raws = e.get("quotes_raw") or list(quotes)
            if len(raws) != len(quotes):
                raws = list(quotes)
            new_quotes, any_fix = [], False
            for raw in raws:
                old = hidx.get(raw)
                if old is None:
                    for hr, hf in hidx.items():      # 原话是对话里某句的一截
                        if len(raw) >= 6 and raw in hr:
                            off = hr.index(raw)
                            old = [dict(f, s=f["s"] - off) for f in hf
                                   if f.get("by") == "llm" and off <= f["s"] < off + len(raw)]
                            break
                fixes = refix(raw, old)
                new_quotes.append(render(raw, fixes) if fixes else raw)
                any_fix = any_fix or bool(fixes)
            before = json.dumps([e.get("quotes"), e.get("summary"), e.get("question"), e.get("follow_ups")], ensure_ascii=False)
            e["quotes"] = new_quotes
            if any_fix:
                e["quotes_raw"] = raws
            else:
                e.pop("quotes_raw", None)
            for k in ("summary", "question"):
                if e.get(k):
                    e[k] = _fix_derived(e[k])
            if e.get("follow_ups"):
                e["follow_ups"] = [_fix_derived(x) for x in e["follow_ups"]]
            after = json.dumps([e.get("quotes"), e.get("summary"), e.get("question"), e.get("follow_ups")], ensure_ascii=False)
            if before != after:
                n += 1
                _log("story_entry:" + e.get("id", ""), before, after)
        if n:
            story.save_entries(ents)
    return n


def apply_derived():
    """记忆、日报、题目、书：只改确认过的错写。返回 {种类: 改动条数}。"""
    import memory_store
    import story
    out = {}
    # 记忆
    with file_lock(memory_store.FACTS_PATH):
        facts = memory_store.load_facts()
        k = 0
        for f in facts:
            new = _fix_derived(f.get("text", ""))
            if new != f.get("text"):
                _log("fact", f["text"], new)
                f["text"] = new
                k += 1
        if k:
            _write_json(memory_store.FACTS_PATH, facts, indent=2)
    out["facts"] = k
    # 日报
    k = 0
    if os.path.isdir(memory_store.REPORTS_DIR):
        for name in sorted(os.listdir(memory_store.REPORTS_DIR)):
            if not name.endswith(".json"):
                continue
            p = os.path.join(memory_store.REPORTS_DIR, name)
            try:
                with open(p, encoding="utf-8") as fh:
                    rep = json.load(fh)
            except Exception:
                continue
            new = _fix_derived(rep.get("text", ""))
            if new != rep.get("text"):
                _log("report:" + name[:-5], rep["text"], new)
                rep["text"] = new
                _write_json(p, rep, indent=2)
                k += 1
    out["reports"] = k
    # 题库
    k = 0
    with file_lock(story.Q_PATH):
        qs = story._load_q()
        for q in qs:
            for key in ("text", "parent"):
                v = q.get(key)
                if isinstance(v, str) and v:
                    new = _fix_derived(v)
                    if new != v:
                        q[key] = new
                        k += 1
        if k:
            story._save_q(qs)
    out["questions"] = k
    # 书：章节文字先直接改掉（马上能看到）；故事条目变了，每晚重写时会按新原话整章重写
    k = 0
    try:
        import book as book_mod
        if os.path.exists(book_mod.BOOK_PATH):
            with open(book_mod.BOOK_PATH, encoding="utf-8") as fh:
                b = json.load(fh)
            for ch in b.get("chapters", []):
                for p in ch.get("paragraphs", []):
                    new = _fix_derived(p.get("text", ""))
                    if new != p.get("text"):
                        p["text"] = new
                        k += 1
                ch["title"] = _fix_derived(ch.get("title", ""))
                for t in ch.get("threads", []):
                    for key in ("text", "question"):
                        if t.get(key):
                            t[key] = _fix_derived(t[key])
            if k:
                _log("book", "", f"{k} 段")
                book_mod._save_book(b)
    except Exception:
        traceback.print_exc()
    out["book"] = k
    return out


# ---------------- DeepSeek 逐处判断（maybe 类）----------------
JUDGE_PROMPT = """妈妈（{elder_brief}）在和儿子{child}的 AI 替身聊天，下面是她说的话的语音识别结果，有错字、重复、断句乱。
「{right}」是{note}。识别有时会把「{right}」听成读音相近的「{wrong}」。

下面每一处都给了两种读法：A 是识别出来的「{wrong}」，B 是换成「{right}」。请判断她当时说的是哪个。
- 默认选 A。只有 B 明显更通顺、A 在这里讲不通的时候，才选 B。
- 「{wrong}」本身意思讲得通（比如真在说吃的、真在问是谁、真在说睡觉），选 A。
- 妈妈常在句子开头或末尾单独喊一声家里人的名字（比如「{child}」）；「{wrong}」孤零零地出现在句首、句尾或逗号之间、和前后文接不上，又正好像在喊人或说那个名字时，选 B。

严格只输出 JSON：{{"B": ["编号", ...]}}，只列选 B 的编号；都选 A 就输出 {{"B": []}}。

{lines}
"""


def _judge_batch(term, variant, batch, model=None):
    """batch: [(句子key, 原文, [位置...])]。返回 {句子key: [判成错写的位置...]}。
    每一处给 A/B 两种读法让模型挑（只问「是不是错」它几乎全答是）。"""
    import deepseek_client
    lines, ids = [], {}
    n = 0
    for k, raw, poss in batch:
        for s in poss:
            n += 1
            a, b = max(0, s - 24), min(len(raw), s + len(variant) + 24)
            ctx_a = raw[a:s] + "【" + variant + "】" + raw[s + len(variant):b]
            ctx_b = raw[a:s] + "【" + term["right"] + "】" + raw[s + len(variant):b]
            lines.append(f"{n}. A：{ctx_a}\n   B：{ctx_b}")
            ids[str(n)] = (k, s)
    prompt = family.adapt_prompt(JUDGE_PROMPT).format(
        elder_brief=family.ELDER_BRIEF, child=family.CHILD_NAME, wrong=variant,
        right=term["right"], note=term.get("note") or "一个人名",
                                 lines="\n".join(lines))
    raw = deepseek_client._chat([{"role": "user", "content": prompt}], temperature=0.0,
                                model=model or JUDGE_MODEL, timeout=600).strip()
    if raw.startswith("```"):
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    marks = json.loads(raw).get("B", [])
    res = {k: [] for k, _r, _p in batch}
    for m in marks:
        hit = ids.get(str(m).strip())
        if hit:
            res[hit[0]].append(hit[1])
    return res


PROBE = 12   # 自动找来的写法，判过不到 REJECT_MIN_JUDGED 处时，一晚只试判这么多句（多半是巧合，不像就放弃）


def judge_pending(st, budget=NIGHT_BUDGET, log=print, auto_budget=AUTO_NIGHT_BUDGET):
    """把还没判过的 maybe 写法交给 DeepSeek 判，判完写回对话记录。返回判了多少句。
    管理员确认的写法用 JUDGE_MODEL、一晚最多 budget 句；自动找来的写法用 AUTO_JUDGE_MODEL、先试判、一晚最多 auto_budget 句。
    各个写法轮流排，免得一个大户（「女人」一千多句）把一晚的额度占满。"""
    import memory_store
    book = load_book()
    items = [it for it in memory_store.iter_history_raw() if it.get("role") == "user"]
    queues = {"manual": [], "auto": []}   # 每个写法一个队列：[(term, v, key, raw, poss), ...]
    for t in book["terms"]:
        autos = set(t.get("auto", []))
        for v in t.get("maybe", []):
            jk = f"{t['id']}|{v}"
            rec0 = st["judged"].setdefault(jk, {"yes": 0, "no": 0, "keys": {}})
            done = rec0["keys"]
            is_auto = v in autos
            cap = PROBE if is_auto and rec0["yes"] + rec0["no"] < REJECT_MIN_JUDGED else None
            q = []
            for it in reversed(items):          # 新的先判
                if cap is not None and len(q) >= cap:
                    break
                raw = it.get("asr_raw") or it.get("text") or ""
                if v not in raw:
                    continue
                key = line_key(it)
                if key in done:
                    continue
                taken = [(f["s"], f["s"] + len(f["from"])) for f in find_fixes(raw)]
                poss = [m.start() for m in re.finditer(re.escape(v), raw)
                        if not any(m.start() < y and x < m.start() + len(v) for x, y in taken)]
                if poss:
                    q.append((t, v, key, raw, poss))
            if q:
                queues["auto" if is_auto else "manual"].append(q)

    def _round_robin(qs, limit):
        out = []
        while qs and len(out) < limit:
            for q in list(qs):
                if not q:
                    qs.remove(q)
                    continue
                take = q[:JUDGE_BATCH]
                del q[:JUDGE_BATCH]
                out.extend(take[:max(limit - len(out), 0)])
                if len(out) >= limit:
                    break
        return out

    plan = [(JUDGE_MODEL, _round_robin(queues["manual"], budget)),
            (AUTO_JUDGE_MODEL, _round_robin(queues["auto"], auto_budget))]
    verdicts = {}   # key -> [(term, v, s)]
    judged = 0
    for model, todo in plan:
        groups = {}
        for t, v, key, raw, poss in todo:
            groups.setdefault((t["id"], v), []).append((key, raw, poss))
        for (tid, v), rows in groups.items():
            t = _term(book, tid)
            rec = st["judged"][f"{tid}|{v}"]
            for i in range(0, len(rows), JUDGE_BATCH):
                batch = rows[i:i + JUDGE_BATCH]
                try:
                    res = _judge_batch(t, v, batch, model=model)
                except Exception as exc:
                    log(f"[typo] DeepSeek 判断失败（{t['right']}/{v}，{model}）：{exc}")
                    break
                for key, raw, poss in batch:
                    yes = res.get(key, [])
                    rec["keys"][key] = yes
                    rec["yes"] += len(yes)
                    rec["no"] += len(poss) - len(yes)
                    for s0 in yes:
                        verdicts.setdefault(key, []).append((t, v, s0))
                judged += len(batch)
                _save_state(st)       # 判一批存一次，中途断了也不白判
    # 写回对话记录
    if verdicts:
        def _fn(its):
            ch = False
            for it in its:
                if it.get("role") != "user":
                    continue
                key = line_key(it)
                if key not in verdicts:
                    continue
                raw = it.get("asr_raw") or it.get("text") or ""
                old = list(it.get("fixes") or [])
                for t, v, s in verdicts[key]:
                    old.append({"s": s, "from": v, "to": t["right"], "t": t["id"], "by": "llm", "at": _now()})
                it["fixes"] = old
                it["asr_raw"] = raw
                if fix_item(it, "asr_raw", "text", "fixes", _veto_of(st, key)):
                    ch = True
            return ch
        memory_store.rewrite_history(_fn)
    return judged


def promote_and_retire(st, log=print):
    """判过足够多次的 maybe：几乎全错 → wrong（不是常用词才升级），几乎全对 → rejected。"""
    book = load_book()
    changed = False
    for t in book["terms"]:
        for v in list(t.get("maybe", [])):
            rec = st["judged"].get(f"{t['id']}|{v}")
            if not rec:
                continue
            total = rec["yes"] + rec["no"]
            if rec["yes"] >= PROMOTE_MIN_YES and rec["yes"] / max(total, 1) >= PROMOTE_RATIO and not _common_word(v):
                t["maybe"].remove(v)
                t["wrong"].append(v)
                log(f"[typo] 「{v}」判了 {total} 处、{rec['yes']} 处是「{t['right']}」→ 以后直接改")
                changed = True
            elif total >= REJECT_MIN_JUDGED and rec["yes"] / total < REJECT_RATIO:
                t["maybe"].remove(v)
                t.setdefault("rejected", []).append(v)
                log(f"[typo] 「{v}」判了 {total} 处、只有 {rec['yes']} 处是「{t['right']}」→ 不再管")
                changed = True
    if changed:
        save_book(book)
    return changed


def discover_all(log=print):
    """读音查找：每个词在原识别文字里找近音写法，放进 maybe（交给 DeepSeek 判）。"""
    import memory_store
    texts = [it.get("asr_raw") or it.get("text") or "" for it in memory_store.iter_history_raw() if it.get("role") == "user"]
    book = load_book()
    added = 0
    for t in book["terms"]:
        found = discover(t, texts)
        for v, c in sorted(found.items(), key=lambda kv: -kv[1]):
            t.setdefault("maybe", []).append(v)
            t.setdefault("auto", []).append(v)    # 自动找来的：用更谨慎的模型判
            added += 1
            log(f"[typo] 读音查找：「{v}」×{c} 像「{t['right']}」，交给 DeepSeek 逐处判断")
    if added:
        save_book(book)
    return added


# ---------------- 一次完整运行 ----------------
_RUN_LOCK = threading.Lock()


def run(judge=True, discover_new=True, budget=NIGHT_BUDGET, log=print, wait=False, auto_budget=AUTO_NIGHT_BUDGET):
    """确认错写全量重算 → 读音查找 → DeepSeek 判一批 → 升级/放弃 → 再全量重算并改派生内容。
    同一时间只跑一个（跨进程用文件锁）；wait=True 时等前一个跑完再跑（管理台改了词表，必须按新词表再算一遍）。"""
    if not _RUN_LOCK.acquire(blocking=wait):
        return {"skipped": "already running"}
    try:
        with file_lock(LOCK_PATH):
            t0 = time.time()
            st = _load_state()
            res = {"started": _now()}
            res["history"] = apply_history(st)
            if discover_new:
                res["discovered"] = discover_all(log)
            if judge:
                res["judged"] = judge_pending(st, budget, log, auto_budget=auto_budget)
                promote_and_retire(st, log)
                res["history"] += apply_history(st)
            res["story"] = apply_story()
            res.update(apply_derived())
            if res["story"]:
                try:
                    import story
                    story.compile_biography()
                except Exception:
                    traceback.print_exc()
            res["pending"] = pending_count(st)
            res["seconds"] = round(time.time() - t0, 1)
            st["runs"].append(res)
            _save_state(st)
            log(f"[typo] {res}")
            return res
    finally:
        _RUN_LOCK.release()


def run_async(**kw):
    kw.setdefault("wait", True)
    th = threading.Thread(target=lambda: run(**kw), daemon=True)
    th.start()
    return th


def pending_count(st=None):
    import memory_store
    st = st or _load_state()
    book = load_book()
    n = 0
    items = [it for it in memory_store.iter_history_raw() if it.get("role") == "user"]
    for t in book["terms"]:
        for v in t.get("maybe", []):
            done = st["judged"].get(f"{t['id']}|{v}", {}).get("keys", {})
            n += sum(1 for it in items if v in (it.get("asr_raw") or it.get("text") or "") and line_key(it) not in done)
    return n


# ---------------- 管理台 ----------------
def summary():
    """管理页用：词表 + 每个写法改了多少处 + 最近的修改。"""
    import memory_store
    book = load_book()
    st = _load_state()
    counts = {}
    recent = []
    total_lines = 0
    for it in memory_store.iter_history_raw():
        if it.get("role") != "user" or not it.get("fixes"):
            continue
        total_lines += 1
        for f in it["fixes"]:
            k = (f["t"], f["from"])
            counts[k] = counts.get(k, 0) + 1
        recent.append(it)
    terms = []
    for t in book["terms"]:
        def _v(lst, kind, term=t):
            out = []
            for v in lst:
                rec = st["judged"].get(f"{term['id']}|{v}", {})
                out.append({"text": v, "kind": kind, "fixed": counts.get((term["id"], v), 0),
                            "judged_yes": rec.get("yes", 0), "judged_no": rec.get("no", 0)})
            return out
        terms.append({"id": t["id"], "right": t["right"], "note": t.get("note", ""), "source": t.get("source", ""),
                      "added_at": t.get("added_at", ""),
                      "variants": _v(t.get("wrong", []), "wrong") + _v(t.get("maybe", []), "maybe") + _v(t.get("rejected", []), "rejected"),
                      "fixed": sum(c for (tid, _v2), c in counts.items() if tid == t["id"])})
    rec_out = []
    for it in recent[-40:][::-1]:
        rec_out.append({"key": line_key(it), "ts": it.get("ts"), "raw": it.get("asr_raw"), "text": it.get("text"),
                        "fixes": it.get("fixes"), "audio": it.get("audio")})
    return {"terms": terms, "lines_fixed": total_lines, "recent": rec_out,
            "last_run": (st["runs"][-1] if st["runs"] else None), "running": _RUN_LOCK.locked()}


def _norm_list(xs):
    out = []
    for x in xs or []:
        x = re.sub(r"\s+", "", str(x))
        if x and x not in out:
            out.append(x)
    return out


def add_term(right, wrong=(), note=""):
    """管理员确认一个词。已有就合并写法。返回词。"""
    right = re.sub(r"\s+", "", str(right or ""))
    if not right or len(right) > 12:
        raise ValueError("正确的词要写 1–12 个字")
    wrong = [w for w in _norm_list(wrong) if w != right]
    book = load_book()
    others = {t["right"] for t in book["terms"]}
    t = next((x for x in book["terms"] if x["right"] == right), None)
    if t is None:
        t = {"id": f"t{book.get('next_id', len(book['terms']) + 1)}", "right": right, "note": note or "",
             "wrong": [], "maybe": [], "rejected": [], "added_at": _now(), "source": "admin"}
        book["next_id"] = int(t["id"][1:]) + 1
        book["terms"].append(t)
    elif note:
        t["note"] = note
    for w in wrong:
        if w in others:
            continue
        for lst in ("maybe", "rejected"):
            if w in t.get(lst, []):
                t[lst].remove(w)
        if w not in t["wrong"]:
            t["wrong"].append(w)
    save_book(book)
    return t


def set_variant(tid, variant, kind):
    """改一个写法的类别：wrong（直接改）/ maybe（看上下文）/ rejected（不算错，撤销已做的修改）/ delete。"""
    book = load_book()
    t = _term(book, tid)
    if not t:
        raise ValueError("没有这个词")
    for lst in ("wrong", "maybe", "rejected"):
        t.setdefault(lst, [])
        if variant in t[lst]:
            t[lst].remove(variant)
    if kind in ("wrong", "maybe", "rejected"):
        t[kind].append(variant)
    if kind == "maybe":   # 重新判
        st = _load_state()
        st["judged"].pop(f"{tid}|{variant}", None)
        _save_state(st)
    save_book(book)
    return t


def delete_term(tid):
    book = load_book()
    book["terms"] = [t for t in book["terms"] if t["id"] != tid]
    save_book(book)


def veto(key, s, frm):
    """单独撤销一句话里的一处修改（以后也不再改这一处）。"""
    st = _load_state()
    lst = st["veto"].setdefault(key, [])
    if [int(s), frm] not in lst:
        lst.append([int(s), frm])
    _save_state(st)


def revert_derived(kinds_prefix=None):
    """把派生内容恢复成第一次纠正前的样子（撤销某个词以后，再跑一遍 run 会按新词表重改）。
    只恢复「现在的内容和当时改完的一样」的条目，之后被别的流程改过的不动。"""
    if not os.path.exists(LOG_PATH):
        return 0
    first_before, last_after = {}, {}
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            first_before.setdefault(r["kind"], r["before"])
            last_after[r["kind"]] = r["after"]
    import memory_store
    import story
    n = 0
    # 记忆：按文本对应
    with file_lock(memory_store.FACTS_PATH):
        facts = memory_store.load_facts()
        pairs = []
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r["kind"] == "fact":
                    pairs.append((r["after"], r["before"]))
        back = {}
        for after, before in pairs:
            back[after] = back.get(before, before)
        k = 0
        for fct in facts:
            if fct.get("text") in back:
                fct["text"] = back[fct["text"]]
                k += 1
        if k:
            _write_json(memory_store.FACTS_PATH, facts, indent=2)
        n += k
    # 日报
    for kind, before in first_before.items():
        if not kind.startswith("report:"):
            continue
        p = os.path.join(memory_store.REPORTS_DIR, kind[7:] + ".json")
        try:
            with open(p, encoding="utf-8") as fh:
                rep = json.load(fh)
        except Exception:
            continue
        if rep.get("text") == last_after.get(kind):
            rep["text"] = before
            _write_json(p, rep, indent=2)
            n += 1
    # 故事条目的概括/问题（原话本身有 quotes_raw，run 会重算）
    with file_lock(story.E_PATH):
        ents = story.load_entries()
        k = 0
        for e in ents:
            kind = "story_entry:" + e.get("id", "")
            if kind not in first_before:
                continue
            cur = json.dumps([e.get("quotes"), e.get("summary"), e.get("question"), e.get("follow_ups")], ensure_ascii=False)
            if cur == last_after.get(kind):
                q, s, qu, fu = json.loads(first_before[kind])
                e["summary"], e["question"], e["follow_ups"] = s, qu, fu
                k += 1
        if k:
            story.save_entries(ents)
        n += k
    os.replace(LOG_PATH, LOG_PATH + ".reverted-" + time.strftime("%Y%m%d-%H%M%S"))
    return n


def undo_and_rerun(**kw):
    """撤销（删词、把写法改成不算错）以后调用：派生内容先恢复，再按当前词表整体重算。"""
    with _RUN_LOCK:
        with file_lock(LOCK_PATH):
            revert_derived()
    kw.setdefault("wait", True)
    return run(judge=False, discover_new=False, **kw)


if __name__ == "__main__":
    # venv/bin/python3 typo_book.py run [--no-judge] [--budget N]   完整跑一遍
    # venv/bin/python3 typo_book.py dry                              只看会改多少（不写文件）
    # venv/bin/python3 typo_book.py discover                         只做读音查找，打印候选（不写文件）
    args = sys.argv[1:]
    cmd = args[0] if args else "dry"
    if cmd == "run":
        budget = int(args[args.index("--budget") + 1]) if "--budget" in args else NIGHT_BUDGET
        print(run(judge="--no-judge" not in args, discover_new="--no-discover" not in args, budget=budget))
    elif cmd == "discover":
        import memory_store
        texts = [it.get("asr_raw") or it.get("text") or "" for it in memory_store.iter_history_raw() if it.get("role") == "user"]
        for t in load_book()["terms"]:
            found = discover(t, texts)
            if found:
                print(t["right"], "←", " ".join(f"{v}×{c}" for v, c in sorted(found.items(), key=lambda kv: -kv[1])[:20]))
    else:
        import memory_store
        n = lines = 0
        per = {}
        for it in memory_store.iter_history_raw():
            if it.get("role") != "user":
                continue
            fx = find_fixes(it.get("asr_raw") or it.get("text") or "")
            if fx:
                lines += 1
                n += len(fx)
                for f in fx:
                    per[f["to"]] = per.get(f["to"], 0) + 1
        print(f"确认错写会改 {lines} 句、{n} 处：", per)
