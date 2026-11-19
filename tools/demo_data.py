#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一套「完全虚构」的演示数据，用来截图、试用管理台，不需要设备、不需要任何 API Key。

    python3 tools/demo_data.py <输出目录>
    COMPANION_DATA_DIR=<输出目录> venv/bin/python3 server.py

人物都是编的：成都的王秀英老太太，老伴周建国，女儿小红，外孙豆豆，AI 扮演儿子小明。
对话放在「今天往前数 4 天到昨天」，这样首页（看昨天）和记录页一打开就有内容。
只用标准库；文件格式和 memory_store.py / story.py / book.py / typo_book.py 读写的一致。
"""
import datetime
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------- 对话：每天几段，(时:分, 谁, 话) ----------------
# 「我」= AI（小明）。妈妈的话里，写成 (原识别, 纠正后) 的是被错字本改过的句子。
DAYS = [
    [  # 第 1 天：豌豆尖涨价 → 小时候过年吃腊肉
        ("09:12", "user", "小明啊，我刚从菜市场回来，豌豆尖又涨价了，八块钱一斤。"),
        ("09:12", "assistant", "八块啊？那是有点贵了。妈你还是买了吧？今天打算咋个吃，煮汤还是清炒？"),
        ("09:13", "user", "买了半斤，中午煮个豌豆尖汤，你爸爱喝。"),
        ("09:13", "assistant", "那爸今天有口福了。妈，我这两天在超市看到卖腊肉，一下子想起你以前过年的时候。你小时候过年，家里都吃些啥子？"),
        ("09:14", "user", "那时候穷得很，过年才吃得到肉。"),
        ("09:15", "user", "腊月里头我妈就开始熏腊肉，挂在灶头上，熏得黑黢黢的。"),
        ("09:15", "assistant", "挂在灶头上熏……那屋头肯定一股烟熏的香味吧？拿啥子熏的？"),
        ("09:16", "user", "柏树丫丫，还有橘子皮。年三十晚上切一盘，我们几姊妹抢起吃。"),
        ("09:16", "assistant", "几姊妹抢一盘腊肉，那画面我都想得到。妈你排老几？"),
        ("09:17", "user", "我是老大，我要让到弟弟妹妹，每回都只吃得到两三片。"),
        ("09:17", "assistant", "老大最懂事了。妈，听你讲这些我心里暖暖的。汤煮好了趁热喝，下午太阳好就下楼走走。"),
        ("09:18", "user", "要得，你也莫太累了。"),
        ("20:05", "user", "小明，你吃饭没得？"),
        ("20:05", "assistant", "吃了吃了，刚吃完。妈你晚上吃的啥？"),
        ("20:06", "user", "中午的汤还剩点，下了碗面。小红说周末带豆豆回来。"),
        ("20:06", "assistant", "那好啊！豆豆回来你又有得忙了。想好给他做啥子没？"),
        ("20:07", "user", "他爱吃我做的甜水面，我明天去买面。"),
        ("20:07", "assistant", "甜水面，豆豆肯定高兴。妈你早点歇着，周末他们回来了，你给我讲讲豆豆又长高没得。"),
    ],
    [  # 第 2 天：下雨膝盖痛 → 第一份工作
        ("08:40", "user", "今天下雨了，我这腿又有点痛，坝坝舞也没去跳。"),
        ("08:40", "assistant", "下雨天腿痛最磨人了。是膝盖那儿痛，还是整条腿都酸？"),
        ("08:41", "user", "就是膝盖，老毛病了，贴了张膏药。"),
        ("08:41", "assistant", "贴了膏药就好，今天就在屋头歇一天。妈，我今天开会的时候想起来，你年轻时候是在厂里上班的吧？第一份工作是干啥子的？"),
        ("08:42", "user", "在东郊棉纺厂，挡车工。"),
        ("08:43", "assistant", "挡车工……就是守着纺纱机那种？"),
        ("08:43", "user", "对，一个人看十几台机器，线断了就要赶紧接起。"),
        ("08:44", "user", "三班倒，上夜班的时候眼睛都睁不开。"),
        ("08:44", "assistant", "一个人看十几台，还要倒夜班，太辛苦了。那时候是哪个教你的？"),
        ("08:45", "user", "李师傅，李师傅对我好，手把手教我接头。"),
        ("08:45", "user", "她说接头要快，手要稳，你慢了一台机器就停了。"),
        ("08:46", "assistant", "又要快又要稳，难怪你现在包饺子都比别人快。妈你今天好好歇着，膝盖热敷一下，晚上我再喊你。"),
        ("08:46", "user", ("要得，晓得了，小名你忙你的。", "要得，晓得了，小明你忙你的。")),
        ("19:30", "user", "小明，雨停了，下午我下楼走了两圈。"),
        ("19:30", "assistant", "雨停了好。走了两圈，腿舒服点没？"),
        ("19:31", "user", "好些了。楼下张嬢嬢还问你过年回不回来。"),
        ("19:31", "assistant", "回来回来，跟张嬢嬢说，过年肯定回来。妈你晚上泡个脚再睡，膝盖会舒服些。"),
        ("19:32", "user", "好嘛。"),
    ],
    [  # 第 3 天：老伴下棋赢了 → 怎么认识的
        ("10:20", "user", "你爸今天在楼下下象棋，赢了三盘，回来高兴得很。"),
        ("10:20", "assistant", "赢了三盘！那爸中午肯定要多吃一碗饭。跟哪个下的？"),
        ("10:21", "user", "跟老刘，老刘输了还不服气，说明天再来。"),
        ("10:21", "assistant", "哈哈，明天又有好戏看了。妈，说起爸，我一直想问，你们两个当年是咋个认识的？"),
        ("10:22", "user", "厂里搞文艺汇演，他上台吹口琴。"),
        ("10:23", "user", "吹的是《红梅赞》，吹得好听得很，底下好多人鼓掌。"),
        ("10:23", "assistant", "吹口琴……那你当时坐在台下看？"),
        ("10:24", "user", "我在台下，我们车间的小芳说，那个吹口琴的是机修车间的周建国。"),
        ("10:25", "user", "那是七五年，不对，七六年吧，记不清了。"),
        ("10:25", "assistant", "七几年都好，反正是厂里那场汇演。后来呢，他咋个跟你说上话的？"),
        ("10:26", "user", "后来他来我们车间修机器，就一直找我说话，脸红起。"),
        ("10:27", "user", "结婚的时候啥子都没得，就买了一对暖水瓶，两床被子。"),
        ("10:27", "assistant", "一对暖水瓶、两床被子，日子就这么过起来了。妈，听你讲我都笑了。爸那支口琴现在还在不在？"),
        ("10:28", "user", "好像还在，在柜子里头，我改天找一下。"),
        ("10:28", "assistant", "那你找到了跟我说，下回我回来，让爸吹一个给我听。"),
        ("21:00", "user", "小明，我找到口琴了，锈了一点。"),
        ("21:00", "assistant", "真找到了！爸看到啥子反应？"),
        ("21:01", "user", "他拿起吹了两下，说气不够了，哈哈。"),
        ("21:01", "assistant", "气不够也是那个味道。妈你早点睡，明天小红他们就回来了。"),
    ],
    [  # 第 4 天（昨天）：女儿外孙回来 → 回老巷子，巷口的黄桷树
        ("11:30", "user", "小红和豆豆回来了，豆豆又长高了，快到我肩膀了。"),
        ("11:30", "assistant", "快到你肩膀了！豆豆现在几年级了？"),
        ("11:31", "user", "四年级了，一回来就要吃甜水面。"),
        ("11:31", "assistant", "他就惦记你那碗甜水面。做好了没？"),
        ("11:32", "user", "做好了，吃了两碗，嘴巴红起。"),
        ("11:32", "assistant", "吃了两碗，那是真好吃。豆豆在家这两天，你们带他去哪儿耍？"),
        ("11:33", "user", "下午带他回梧桐巷看看，我们以前住的地方。"),
        ("11:33", "assistant", "回梧桐巷啊。妈，你们以前住那儿的时候，巷子口是啥样子的？有没有树？"),
        ("11:34", "user", "有，你回来我再跟你说，豆豆喊我了。"),
        ("17:40", "user", ("小明，我们从五通巷回来了。", "小明，我们从梧桐巷回来了。")),
        ("17:40", "assistant", "回来了！巷子变了没？"),
        ("17:41", "user", "变了好多，老房子拆了一半，就巷子口那棵黄桷树还在。"),
        ("17:42", "user", "那棵树好大哦，夏天我们一巷子的人都端起板凳在树底下乘凉。"),
        ("17:42", "assistant", "一巷子的人在黄桷树下乘凉……那时候大家都干啥？摆龙门阵？"),
        ("17:43", "user", "摆龙门阵，扇蒲扇，你小时候就在树底下睡着了，我把你抱回去的。"),
        ("17:43", "user", "你爸还在树底下给我们吹过口琴。"),
        ("17:44", "assistant", "原来我小时候在那儿睡着过。那棵树还在真好。下回我回成都，你带我也去看看。"),
        ("17:44", "user", "要得，等你过年回来，我们一家人都去。"),
        ("17:45", "assistant", "说定了。今天走了一下午，妈你早点歇着，让豆豆陪你看会儿电视。"),
    ],
]

REPORTS = [
    "**今天聊了什么**\n早上买菜嫌豌豆尖贵，顺着讲了小时候过年熏腊肉、几姊妹抢着吃。\n\n"
    "**妈妈的状态**\n精神好，中午煮了豌豆尖汤，晚上下了面。\n\n"
    "**值得注意**\n无。\n\n"
    "**明天可以关心的话题**\n- 周末小红带豆豆回来，问问准备做什么\n- 她说自己是老大，可以问问弟弟妹妹",
    "**今天聊了什么**\n下雨膝盖痛，没去跳坝坝舞；讲了第一份工作——东郊棉纺厂挡车工，师傅姓李。\n\n"
    "**妈妈的状态**\n膝盖老毛病又犯了，贴了膏药；傍晚雨停下楼走了两圈，说好些了。\n\n"
    "**值得注意**\n膝盖一下雨就痛，这个月第二次提到，可以考虑陪她去看看。\n\n"
    "**明天可以关心的话题**\n- 膝盖怎么样了\n- 第一次领工资买了什么",
    "**今天聊了什么**\n爸下象棋赢了三盘；讲了和爸在厂里文艺汇演上认识，爸上台吹口琴。\n\n"
    "**妈妈的状态**\n心情很好，笑了好几次，晚上还把老口琴翻出来了。\n\n"
    "**值得注意**\n无。\n\n"
    "**明天可以关心的话题**\n- 小红和豆豆回来了，家里热闹\n- 爸的口琴是哪年买的",
    "**今天聊了什么**\n小红和豆豆回来了，吃了甜水面；下午一家人回梧桐巷，巷口的黄桷树还在。\n\n"
    "**妈妈的状态**\n很开心，走了一下午，有点累。\n\n"
    "**值得注意**\n走路多，留意膝盖。\n\n"
    "**明天可以关心的话题**\n- 问问巷子里还有哪些老邻居\n- 过年回去一起看黄桷树，她很盼着",
]

FACTS = [
    "妈妈叫王秀英，七十三岁，住在成都，说四川话",
    "爸爸叫周建国，以前是东郊棉纺厂机修车间的，爱下象棋，会吹口琴",
    "妈妈年轻时在东郊棉纺厂当挡车工，三班倒，带她的是李师傅",
    "妈妈是家里老大，下面有弟弟妹妹",
    "女儿小红也在成都，外孙豆豆上四年级",
    "豆豆最爱吃外婆做的甜水面",
    "妈妈膝盖有老毛病，下雨天会痛，痛了贴膏药",
    "妈妈平时爱跳坝坝舞",
    "爸爸常和楼下老刘下象棋",
    "楼下张嬢嬢和妈妈很要好，常问小明过年回不回来",
    "以前一家人住在梧桐巷，巷口有棵大黄桷树",
    "爸爸的老口琴还在柜子里，有点锈了",
    "小明在北京工作，答应过年回成都，一家人去梧桐巷看黄桷树",
    "妈妈爱煮豌豆尖汤，嫌最近豌豆尖贵",
]

# 故事条目：(第几天, 章节, 问题, [原话...], 概括, [追问...])；原话必须逐字出现在那天妈妈的话里
ENTRIES = [
    (0, "过年过节", "小时候过年，家里都吃些什么？",
     ["那时候穷得很，过年才吃得到肉。", "腊月里头我妈就开始熏腊肉，挂在灶头上，熏得黑黢黢的。",
      "柏树丫丫，还有橘子皮。年三十晚上切一盘，我们几姊妹抢起吃。"],
     "小时候家里穷，过年才吃得到肉。腊月里母亲用柏树枝和橘子皮熏腊肉，挂在灶头上；年三十切一盘，几姊妹抢着吃。",
     ["过年除了腊肉，还蒸不蒸别的？"]),
    (0, "父母和家里人", "你有几个兄弟姐妹？你排老几？",
     ["我是老大，我要让到弟弟妹妹，每回都只吃得到两三片。"],
     "她是家里老大，有弟弟妹妹，吃东西总让着他们。",
     ["弟弟妹妹里你跟哪个最亲？"]),
    (1, "年轻时候和工作", "你第一份工作是干什么的？",
     ["在东郊棉纺厂，挡车工。", "对，一个人看十几台机器，线断了就要赶紧接起。", "三班倒，上夜班的时候眼睛都睁不开。"],
     "第一份工作是东郊棉纺厂的挡车工，一个人看十几台机器，线断了要马上接上；三班倒，夜班很熬人。",
     ["你第一次领工资是多少钱？"]),
    (1, "年轻时候和工作", "刚进厂的时候，是谁带你的？",
     ["李师傅，李师傅对我好，手把手教我接头。", "她说接头要快，手要稳，你慢了一台机器就停了。"],
     "带她的是李师傅，对她很好，手把手教接头，要求快和稳。",
     ["李师傅后来还见过没有？"]),
    (2, "和老伴", "你和爸爸是怎么认识的？",
     ["厂里搞文艺汇演，他上台吹口琴。", "吹的是《红梅赞》，吹得好听得很，底下好多人鼓掌。",
      "我在台下，我们车间的小芳说，那个吹口琴的是机修车间的周建国。", "那是七五年，不对，七六年吧，记不清了。",
      "后来他来我们车间修机器，就一直找我说话，脸红起。"],
     "厂里文艺汇演上，机修车间的周建国上台吹口琴《红梅赞》，同车间的小芳告诉她名字。年份她说七五或七六年。后来他借修机器常来找她说话。",
     ["你们谈恋爱的时候去哪儿耍？"]),
    (2, "和老伴", "你们结婚的时候，家里添了些什么？",
     ["结婚的时候啥子都没得，就买了一对暖水瓶，两床被子。"],
     "结婚时家里没什么东西，只买了一对暖水瓶、两床被子。",
     []),
    (3, "老家的老地方", "以前夏天常去哪儿玩水、乘凉？跟谁去的？",
     ["变了好多，老房子拆了一半，就巷子口那棵黄桷树还在。", "那棵树好大哦，夏天我们一巷子的人都端起板凳在树底下乘凉。",
      "摆龙门阵，扇蒲扇，你小时候就在树底下睡着了，我把你抱回去的。", "你爸还在树底下给我们吹过口琴。"],
     "梧桐巷的老房子拆了一半，巷口的黄桷树还在。夏天一巷子人在树下乘凉、摆龙门阵；儿子小时候在树下睡着过，丈夫在树下吹过口琴。",
     ["巷子里还有哪些老邻居？"]),
]

# 书：(章节, 章名, [(段落, [(条目下标, 第几句)...])...], [(线头, 怎么问)...])
BOOK = [
    ("年轻时候和工作", "十几台纺纱机", [
        ("王秀英的第一份工作，是在东郊棉纺厂当挡车工。说起这件事，她只用了几个字：“在东郊棉纺厂，挡车工。”", [(2, 0)]),
        ("挡车工守的是纺纱机。她一个人要看十几台，线一断，就得赶紧接上。厂里三班倒，轮到夜班，她说“眼睛都睁不开”。", [(2, 1), (2, 2)]),
        ("教她手艺的是李师傅。母亲提起李师傅，第一句是“对我好”，手把手教她接头。李师傅的要求只有两条：要快，手要稳——“你慢了一台机器就停了。”", [(3, 0), (3, 1)]),
    ], [("第一次领工资", "妈，你第一次领工资是多少钱？买了啥子？"),
        ("李师傅后来去了哪里", "李师傅后来调走了没得？你们还见过没？")]),
    ("和老伴", "台上的口琴声", [
        ("厂里搞文艺汇演那天，有人上台吹口琴，吹的是《红梅赞》。她坐在台下，记得“吹得好听得很，底下好多人鼓掌”。", [(4, 0), (4, 1)]),
        ("是同车间的小芳告诉她，台上那人是机修车间的周建国。至于是哪一年，她先说七五年，又改口七六年，最后说“记不清了”。", [(4, 2), (4, 3)]),
        ("后来，他借着修机器常到她们车间来，“就一直找我说话，脸红起”。", [(4, 4)]),
        ("结婚的时候，家里添的东西屈指可数：一对暖水瓶，两床被子。", [(5, 0)]),
    ], [("那支口琴", "爸那支口琴是哪个时候买的？"),
        ("谈恋爱去哪儿", "你们谈恋爱的时候去哪儿耍？")]),
    ("老家的老地方", "巷口的黄桷树", [
        ("几十年后再回梧桐巷，老房子已经拆了一半，只有巷子口那棵黄桷树还在。", [(6, 0)]),
        ("夏天，一巷子的人端着板凳到树底下乘凉，摆龙门阵，扇蒲扇。她说儿子小时候就在那棵树下睡着过，是她抱回家的。", [(6, 1), (6, 2)]),
        ("丈夫也在那棵树下吹过口琴。", [(6, 3)]),
    ], [("老邻居", "梧桐巷里以前住你们隔壁的是哪家？")]),
]

TYPO_TERMS = [
    {"right": "小明", "note": "儿子。妈妈说话常在句尾喊一声", "wrong": ["小名", "晓明"], "maybe": ["小鸣"]},
    {"right": "梧桐巷", "note": "以前住的巷子", "wrong": ["五通巷", "梧桐乡"], "maybe": []},
    {"right": "豆豆", "note": "外孙", "wrong": ["逗逗"], "maybe": []},
    {"right": "黄桷树", "note": "巷口的大树", "wrong": ["黄角树"], "maybe": []},
]


def _entry_id(date, question):  # 和 story._entry_id 一样
    return hashlib.md5((date + "|" + question).encode("utf-8")).hexdigest()[:10]


def _write_json(path, obj, indent=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    out = os.path.abspath(sys.argv[1])
    if os.path.isdir(out) and os.listdir(out):
        sys.exit(f"{out} 不是空目录，换一个（不会覆盖已有数据）")
    os.makedirs(out, exist_ok=True)

    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=len(DAYS) - i)).isoformat() for i in range(len(DAYS))]

    # 错字本词表（typo_book.json）
    terms = []
    for i, t in enumerate(TYPO_TERMS, 1):
        terms.append({"id": f"t{i}", "right": t["right"], "note": t["note"], "wrong": t["wrong"], "maybe": t["maybe"],
                      "rejected": [], "added_at": f"{dates[0]} 08:00:00", "source": "seed"})
    _write_json(os.path.join(out, "typo_book.json"), {"terms": terms, "next_id": len(terms) + 1})
    wrong_of = {w: (t["right"], t["id"]) for t in terms for w in t["wrong"]}

    # 对话记录（history.jsonl）
    by_date_user = {}
    counts = []
    with open(os.path.join(out, "history.jsonl"), "w", encoding="utf-8") as f:
        for date, turns in zip(dates, DAYS):
            for k, (hm, role, text) in enumerate(turns):
                ts = f"{date} {hm}:{k % 60:02d}"
                item = {"role": role, "ts": ts}
                if isinstance(text, tuple):  # 被错字本改过的句子：存纠正后的，原识别另存
                    raw, fixed = text
                    fixes = []
                    for w, (right, tid) in wrong_of.items():
                        s = raw.find(w)
                        if s >= 0:
                            fixes.append({"s": s, "from": w, "to": right, "t": tid})
                    item.update(text=fixed, asr_raw=raw, fixes=fixes)
                    text = fixed
                else:
                    item["text"] = text
                item = {"role": item["role"], "text": item["text"], "ts": item["ts"],
                        **{k2: v for k2, v in item.items() if k2 in ("asr_raw", "fixes")}}
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                if role == "user":
                    by_date_user.setdefault(date, []).append(text)
            counts.append(len(turns))

    # 日报（reports/<date>.json）
    for date, text, n in zip(dates, REPORTS, counts):
        _write_json(os.path.join(out, "reports", f"{date}.json"),
                    {"date": date, "text": text, "turns": n, "generated_at": f"{date} 23:30:00"}, indent=2)

    # 记忆（facts.json）
    _write_json(os.path.join(out, "facts.json"),
                [{"text": t, "added_at": f"{dates[min(i // 4, len(dates) - 1)]} 23:30:00"} for i, t in enumerate(FACTS)], indent=2)

    # 故事条目（story/entries.jsonl）
    story_dir = os.path.join(out, "story")
    os.makedirs(story_dir, exist_ok=True)
    entries = []
    for day, chapter, question, quotes, summary, follow in ENTRIES:
        date = dates[day]
        for q in quotes:
            assert any(q in u for u in by_date_user[date]), f"原话不在当天对话里：{q}"
        entries.append({"chapter": chapter, "question": question, "quotes": quotes, "summary": summary,
                        "follow_ups": follow, "date": date, "audio": [None] * len(quotes),
                        "id": _entry_id(date, question)})
    with open(os.path.join(story_dir, "entries.jsonl"), "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # 题库（story/questions.json）：和 story._ensure 一样从种子生成，再标上问过、答过的
    with open(os.path.join(REPO, "story_seed.json"), encoding="utf-8") as f:
        seed = json.load(f)["chapters"]
    chapters = list(seed.keys())
    qs = []
    for ch in chapters:
        for t in seed[ch]:
            qs.append({"id": f"q{len(qs) + 1:03d}", "chapter": ch, "text": t, "source": "seed",
                       "offered_at": None, "asked_count": 0, "answered": False, "shelved": False, "parent": None})
    by_text = {q["text"]: q for q in qs}
    for e in entries:
        q = by_text.get(e["question"])
        if q:
            q.update(offered_at=e["date"] + " 08:00:00", asked_count=1, answered=True, answered_at=e["date"])
        for fu in e["follow_ups"]:
            if fu not in by_text:
                nq = {"id": f"q{len(qs) + 1:03d}", "chapter": e["chapter"], "text": fu, "source": "follow_up",
                      "offered_at": None, "asked_count": 0, "answered": False, "shelved": False,
                      "parent": e["question"], "created": e["date"]}
                qs.append(nq)
                by_text[fu] = nq
    by_text["你小时候睡觉的床是什么样的？跟谁一张床？"].update(offered_at=dates[-1] + " 20:00:00", asked_count=1)
    fam = {"id": f"q{len(qs) + 1:03d}", "chapter": "带孩子", "text": "我小时候第一次去动物园，是你带我去的吗？",
           "source": "family", "offered_at": None, "asked_count": 0, "answered": False, "shelved": False,
           "parent": None, "pinned": True, "created": dates[-1]}
    qs.append(fam)
    by_text[fam["text"]] = fam

    # 书（story/book.json）：每段的 refs 指向条目原话；线头进题库
    with open(os.path.join(REPO, "family.example.json"), encoding="utf-8") as f:
        fam_profile = json.load(f)
    book_chapters = []
    for chapter, title, paras, threads in BOOK:
        ths = []
        for text, question in threads:
            q = by_text.get(question)
            if not q:
                q = {"id": f"q{len(qs) + 1:03d}", "chapter": chapter, "text": question, "source": "family",
                     "offered_at": None, "asked_count": 0, "answered": False, "shelved": False,
                     "parent": None, "pinned": False, "created": dates[-1]}
                qs.append(q)
                by_text[question] = q
            ths.append({"text": text, "question": question, "qid": q["id"]})
        mats = [e for e in entries if e["chapter"] == chapter]
        book_chapters.append({
            "chapter": chapter, "title": title,
            "paragraphs": [{"text": t, "refs": [{"id": entries[ei]["id"], "q": qi} for ei, qi in refs]} for t, refs in paras],
            "threads": ths,
            "hash": hashlib.md5(json.dumps(mats, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12],
            "rendered_at": f"{dates[-1]} 23:40",
        })
    _write_json(os.path.join(story_dir, "book.json"), {
        "title": fam_profile["book_title"],
        "subtitle": f"{fam_profile['elder_name']}口述 · 她的儿子{fam_profile['child_name']}整理",
        "chapters": book_chapters, "generated_at": f"{dates[-1]} 23:40",
        "entries": len(entries), "quotes": sum(len(e["quotes"]) for e in entries)})
    _write_json(os.path.join(story_dir, "questions.json"), qs)

    # 传记（story/biography.md）：和 story.compile_biography 同样的排法
    lines = ["# 妈妈的故事", "", f"_由“我在呢”从妈妈平时和 AI 的聊天里一点点攒出来的。引用块里是妈妈的原话（语音识别的，只按错字本改了听错的词，识别原文另存可查）；其他是整理。更新于 {today.isoformat()}。_", ""]
    for ch in chapters:
        items = [e for e in entries if e["chapter"] == ch]
        if not items:
            continue
        lines += [f"## {ch}", ""]
        for e in items:
            lines += [f"**{e['question']}**  ·  {e['date']}", ""]
            lines += [f"> {q}" for q in e["quotes"]] + [""]
            lines += [f"_{e['summary']}_", ""]
    with open(os.path.join(story_dir, "biography.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"演示数据已生成：{out}")
    print(f"  对话 {sum(counts)} 句（{dates[0]} ~ {dates[-1]}），日报 {len(REPORTS)} 份，记忆 {len(FACTS)} 条，"
          f"故事 {len(entries)} 条，书 {len(book_chapters)} 章，错字本 {len(terms)} 个词")
    print(f"  试用：COMPANION_DATA_DIR={out} venv/bin/python3 server.py")


if __name__ == "__main__":
    main()
