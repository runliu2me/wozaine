#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每天晚上自动生成当天的对话日报（systemd timer 调用，见 systemd/daily-report.timer）。

也可以手动跑：venv/bin/python3 daily_report.py [YYYY-MM-DD]
没有对话的日子不生成；已经生成过且对话条数没变的日子跳过。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deepseek_client
import family
import memory_store
import story

def consolidate_memory():
    """每晚把记忆整理一遍：去琐碎、去重、合并，控制总量，避免记忆越积越多把模型搞乱"""
    facts = [f["text"] for f in memory_store.load_facts()]
    if len(facts) <= 3:
        return
    cleaned = deepseek_client.consolidate_facts(facts)
    if not cleaned:
        print("记忆整理：模型没返回内容，跳过")
        return
    cleaned = cleaned[:MAX_FACTS]
    memory_store.replace_facts(cleaned)
    print(f"记忆整理：{len(facts)} 条 → {len(cleaned)} 条")


MAX_FACTS = 40  # 记忆条数上限：再多就会稀释提示词，模型开始混乱


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--consolidate":
        consolidate_memory()
        return
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y-%m-%d")
    # 错字本：先按确认的错写把今天和以前的内容改一遍（几秒钟），后面的日报、记忆、故事用的就是纠正后的文字；
    # 要 DeepSeek 逐处判断的放在最后跑（要几分钟到几十分钟），判出来的明晚的日报就能用上
    _typo(judge=False)
    try:
        _main_day(date)
    finally:
        _typo(judge=True)


def _typo(judge):
    try:
        import typo_book
        typo_book.run(judge=judge, discover_new=judge, wait=True)
    except Exception as exc:
        print("错字本运行失败：", exc)


def _main_day(date):
    turns = memory_store.history_for_date(date)
    if not turns:
        print(f"{date}: 没有对话，跳过")
        return
    existing = memory_store.load_report(date)
    if existing and existing.get("turns") == len(turns):
        print(f"{date}: 报告已是最新（{len(turns)} 条），跳过")
        return
    text = deepseek_client.summarize_day(date, turns)
    memory_store.save_report(date, text, len(turns))
    print(f"{date}: 已生成报告（{len(turns)} 条对话）")
    consolidate_memory()
    build_story(date, turns)


def build_story(date, turns):
    """妈妈的故事：把当天她讲的过去的事摘出来（原话不改），追问入题库，重新整理传记"""
    try:
        asked, retold = [], []
        try:
            import json as _json
            import urllib.request as _ur
            req = _ur.Request(os.environ.get("XIAOZHI_STATUS_URL", "http://127.0.0.1:3096/xiaozhi/ota/status"),
                              headers={"X-Ctl-Token": os.environ.get("XIAOZHI_CTL_TOKEN", "")})
            with _ur.urlopen(req, timeout=10) as r:
                todays = [a for a in _json.loads(r.read().decode())["story_asked"] if a.get("date") == date]
                asked = [a["text"] for a in todays if a.get("kind", "ask") == "ask"]
                retold = [a["entry_id"] for a in todays if a.get("kind") == "retell"]
        except Exception as exc:
            print("取今天问过的故事题失败：", exc)
        n = story.extract_day(date, turns, todays_q=(asked[-1] if asked else None), retold_id=(retold[-1] if retold else None))
        m = story.generate_personal_questions(memory_store.load_facts())
        story.compile_biography()
        print(f"{family.ELDER_LABEL}的故事：{date} 新增 {n} 条，按{family.ELDER_PRONOUN}讲过的事新出 {m} 道题")
        try:
            import book
            k = book.render()
            print(f"《{family.BOOK_TITLE}》：重写了 {k} 章")
        except Exception as exc:
            print("书渲染失败：", exc)
    except Exception as exc:
        print("故事整理失败：", exc)


if __name__ == "__main__":
    main()
