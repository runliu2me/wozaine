#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错字本的离线测试（不调用任何模型、不花钱）：venv/bin/python3 tests/test_typo_book.py"""
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="typo-test-")
os.environ["COMPANION_DATA_DIR"] = TMP
# 测试用的虚构词表（不依赖仓库里的 typo_seed.json）
SEED = os.path.join(TMP, "typo_seed.json")
with open(SEED, "w", encoding="utf-8") as _f:
    json.dump({"terms": [
        {"right": "张伟", "note": "儿子", "wrong": ["张威", "章伟"], "maybe": ["女人", "牛肉", "有人"]},
        {"right": "王芳芳", "note": "老同事", "wrong": ["王芳"]},
        {"right": "栗水", "note": "地名", "wrong": ["丽水"]},
    ]}, _f, ensure_ascii=False)
os.environ["COMPANION_TYPO_SEED"] = SEED
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import typo_book  # noqa: E402
import memory_store  # noqa: E402
import story  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def main():
    # 1. 确认错写：替换、保护正确词、结巴重复
    t, fx = typo_book.correct("张威你什么时候回来？张张威。我和王芳芳，王芳在丽水上班")
    check("替换张威", t == "张伟你什么时候回来？张张伟。我和王芳芳，王芳芳在栗水上班", t)
    check("王芳芳本身不被再改", "王芳芳丽" not in t, t)
    check("修改位置记在原文上", all("s" in f and f["from"] for f in fx))
    check("render 还原", typo_book.render("张威你好", typo_book.find_fixes("张威你好")) == "张伟你好")
    check("没有错字原样返回", typo_book.correct("今天天气很好")[0] == "今天天气很好")
    check("要看上下文的词入库时不改", typo_book.correct("牛肉炒韭菜，女人")[0] == "牛肉炒韭菜，女人")

    # 2. 入库：存纠正后的文字 + 识别原文
    memory_store.append_history("user", "章伟你吃饭了吗", audio="audio/x/1.mp3")
    memory_store.append_history("assistant", "张威是谁")      # 我自己说的话不改
    memory_store.append_history("user", "今天很好")
    items = list(memory_store.iter_history_raw())
    check("入库纠正", items[0]["text"] == "张伟你吃饭了吗" and items[0]["asr_raw"] == "章伟你吃饭了吗", items[0])
    check("助手的话不改", items[1]["text"] == "张威是谁" and "asr_raw" not in items[1])
    check("没改的句子不加字段", "asr_raw" not in items[2] and "fixes" not in items[2])

    # 3. DeepSeek 判过的修改（模拟）：写进 fixes，重算保留；把写法改成「不算错」后撤销
    st = typo_book._load_state()

    def _add_llm(its):
        for it in its:
            if it.get("text") == "今天很好":
                it["text"] = "牛肉妈妈吃过了"
        return True
    memory_store.rewrite_history(_add_llm)

    def _mark(its):
        for it in its:
            if it.get("text") == "牛肉妈妈吃过了":
                it["asr_raw"] = it["text"]
                it["fixes"] = [{"s": 0, "from": "牛肉", "to": "张伟", "t": "t1", "by": "llm"}]
                typo_book.fix_item(it, "asr_raw", "text", "fixes")
        return True
    memory_store.rewrite_history(_mark)
    typo_book.apply_history(st)
    it = [x for x in memory_store.iter_history_raw() if x.get("asr_raw") == "牛肉妈妈吃过了"][0]
    check("DeepSeek 判过的修改保留", it["text"] == "张伟妈妈吃过了", it)
    typo_book.set_variant("t1", "牛肉", "rejected")
    typo_book.apply_history(st)
    it = [x for x in memory_store.iter_history_raw() if (x.get("asr_raw") or x["text"]) == "牛肉妈妈吃过了"][0]
    check("写法改成不算错后撤销", it["text"] == "牛肉妈妈吃过了" and "fixes" not in it, it)
    typo_book.set_variant("t1", "牛肉", "maybe")

    # 4. 单独撤销一处
    key = typo_book.line_key([x for x in memory_store.iter_history_raw() if x.get("asr_raw") == "章伟你吃饭了吗"][0])
    typo_book.veto(key, 0, "章伟")
    typo_book.apply_history(typo_book._load_state())
    it = list(memory_store.iter_history_raw())[0]
    check("单独撤销一处", it["text"] == "章伟你吃饭了吗", it)

    # 5. 新加一个词、删掉一个词
    typo_book.add_term("小丽", ["小立"], "邻居")
    check("加词", typo_book.correct("小立来了")[0] == "小丽来了")
    tid = next(t["id"] for t in typo_book.load_book()["terms"] if t["right"] == "小丽")
    typo_book.delete_term(tid)
    check("删词", typo_book.correct("小立来了")[0] == "小立来了")

    # 6. 故事条目：原话纠正、原文另存；记忆、日报改确认的错写
    os.makedirs(story.STORY_DIR, exist_ok=True)
    story.save_entries([{"id": "e1", "date": "2026-11-01", "question": "张威小时候？", "chapter": "小时候",
                         "quotes": ["张威小时候很乖", "在丽水上班"], "summary": "她说张威小时候很乖"}])
    with open(memory_store.FACTS_PATH, "w", encoding="utf-8") as f:
        json.dump([{"text": "妈妈想张威", "added_at": "x"}], f, ensure_ascii=False)
    memory_store.save_report("2026-11-01", "妈妈提到张威", 3)
    typo_book.apply_story()
    typo_book.apply_derived()
    e = story.load_entries()[0]
    check("故事原话纠正", e["quotes"] == ["张伟小时候很乖", "在栗水上班"], e)
    check("故事原文另存", e.get("quotes_raw") == ["张威小时候很乖", "在丽水上班"], e)
    check("故事概括纠正", e["summary"] == "她说张伟小时候很乖" and e["question"] == "张伟小时候？", e)
    check("记忆纠正", memory_store.load_facts()[0]["text"] == "妈妈想张伟")
    check("日报纠正", memory_store.load_report("2026-11-01")["text"] == "妈妈提到张伟")
    n = typo_book.revert_derived()
    check("派生内容可以恢复", memory_store.load_facts()[0]["text"] == "妈妈想张威" and n >= 2, n)

    # 7. 提示和热词
    check("热词有张伟", typo_book.hotwords()[0] == "张伟")
    check("提示里有错写", "张威" in typo_book.prompt_hint())

    # 8. 读音查找
    term = {"right": "王金宝", "wrong": [], "maybe": [], "rejected": []}
    found = typo_book.discover(term, ["王进宝来了", "王进宝走了", "王进宝好", "今天很好"])
    check("读音查找", found.get("王进宝") == 3, found)
    found = typo_book.discover({"right": "栗水", "wrong": [], "maybe": [], "rejected": []}, ["能睡了"] * 5)
    check("读音查找不乱找", "能睡" not in found, found)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'全部通过' if not FAILS else '失败：' + '、'.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
