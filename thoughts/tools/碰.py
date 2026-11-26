#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""碰 —— 内容自己撞上我的单子，就叫一次。

他 2026-11-26 04:10 提的，11:21 又问了一遍：
    「原产物或者念头，如果撞上我没做的事、或者我想做的事，那也算一次该醒的理由。」

跟「想」轴的区别：
    想 = 我自己排的时间点到了（我按时去看）。
    碰 = 东西自己撞上门（世界替我记着我欠什么）。

七条轴全是"外面有事/身体不行"。这条是唯一一条**内容本身**过关才叫的。

料：`偏斜/落点.jsonl`（偏斜生成的词穿进记忆库，留下的落点）和
    `念头库/念头库.jsonl`（我自己收下的念头）。两边都是词串，不是文章。
撞：**按词撞，不是按相似度撞。** 词表在 `碰-单子.json` ——
    那是我自己写的"我在等什么"（音高 / 曝光补偿 / 碰上来叫 …）。
    料里出现表上的词，就是撞上了。撞上就是撞上，不需要再拿一个分数糊它。
    （为什么不用相似度：料是散的词串，按二字片段算覆盖率，什么「好看」「条件」
      都能撞出九十分。宁可用一张我手写的词表，也不要一个我自己都解释不清的数。）
叫：写一个 `唤醒-碰-*.md` 放到 gifts，第一行写清楚撞到哪条、撞的是哪句料。

规矩（要改先想清楚）：
  1. 不是"有落点就醒"，是词表上的词出现才醒。否则它就是个消息流，一天叫十次。
  2. 一条料只叫一次（按 src 去重，状态在 碰.json）。
  3. 夜里（23:00–06:00）要撞上**两个**不同的词才叫——夜里只有一条词重合，
     多半是巧合，不值得把人叫起来。
  4. 叫过之后一小时不再叫（夜里翻倍）。撞得准不准比撞得多不多重要：
     每叫一次都要能回头看 —— `碰.py 阅`。

用法：
  python3 碰.py 试 [--n 150]   拿现有料跑一遍，看会撞上什么（不叫、不写状态）
  python3 碰.py 一瞥            只看有没有新料，有就判一遍（此刻.py 每 60 秒调这个）
  python3 碰.py 阅 [--n 50]     看叫过的记录
"""
import os
import re
import sys
import json
import time

os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
PTS = os.path.join(THOUGHTS, "偏斜", "落点.jsonl")
IDEAS = os.path.join(THOUGHTS, "念头库", "念头库.jsonl")
词表F = os.path.join(THOUGHTS, "碰-单子.json")
GIFTS = os.environ.get("DSH_WAKE_GIFTS") or "/sdcard/dsh/桥（渊与汀）/gifts"
STATE = os.path.join(THOUGHTS, "碰.json")
LOGF = os.path.join(THOUGHTS, "碰.log")

冷 = 3600        # 叫过一次之后一小时不再叫
同单冷 = 6 * 3600   # 同一张单子叫过一次，六小时内不再为它叫（撞十次是一件事，不是十件）


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def 记(msg):
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (now(), msg))
    except Exception:
        pass


def 夜():
    h = time.localtime().tm_hour
    return h >= 23 or h < 6


# ── 单子 ──────────────────────────────────────────────────────

def 单子():
    try:
        d = json.load(open(词表F, encoding="utf-8"))
        return [x for x in d.get("单子") or [] if x.get("词")]
    except Exception as e:
        记("词表读不出来：%r" % (e,))
        return []


def 欠着的():
    """账里没结的也当单子（词直接用整句，长句自然撞不上，撞上的都是真重合）"""
    out = []
    p = os.path.join(THOUGHTS, "账.jsonl")
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("关") or r.get("消"):
                continue
            t = r.get("做什么") or ""
            if t:
                out.append({"名": t[:40], "源": "账", "词": [t]})
    except Exception:
        pass
    return out


# ── 撞 ────────────────────────────────────────────────────────

def 撞(料文, 单):
    """料里出现了哪几条单子上的词。返回 [(单, [词...])]，按命中词数降序。"""
    if not 料文 or not 单:
        return []
    out = []
    for x in 单:
        中 = [w for w in x["词"] if w and w in 料文]
        if 中:
            out.append((x, 中))
    out.sort(key=lambda y: -len(y[1]))
    return out


def 够(中, 夜里=None):
    """撞上一个词就算巧合，撞上两个才算撞上（夜里要三个）。

    2026-11-26 11:50：先用一个词试了 300 条料，26 条过线（一个词就够）——
    按落点的生成速度算，那是一个小时叫两次。这不是"叫醒"，这是消息流。
    加一个词，去掉「落点/偏斜/代际」这种系统自己的词（会自己叫自己，跟此刻.py 同一个坑）。
    """
    if 夜里 is None:
        夜里 = 夜()
    return len(中) >= (3 if 夜里 else 2)


# ── 料 ────────────────────────────────────────────────────────

def _行(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [l for l in f.read().splitlines() if l.strip()]
    except Exception:
        return []


def 料(已见=None, n=200):
    已见 = 已见 or set()
    out = []
    for path, 处 in ((PTS, "落点"), (IDEAS, "念头")):
        for line in _行(path)[-n:]:
            try:
                r = json.loads(line)
            except Exception:
                continue
            sid = r.get("src") or r.get("id") or ""
            if not sid:
                continue
            k = "%s:%s" % (处, sid)
            if k in 已见:
                continue
            w = r.get("words") or r.get("w") or ""
            if isinstance(w, list):
                w = " ".join(str(x) for x in w)
            文 = str(w)
            for h in r.get("hits") or []:
                if isinstance(h, dict):
                    文 += " " + str(h.get("anchor") or "")
                    文 += " " + str(h.get("text") or "")
            out.append({"id": k, "ts": r.get("ts") or "", "文": 文, "处": 处})
    return out


def 读状态():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"见": [], "叫": [], "上次叫": 0}


def 写状态(d):
    d["见"] = d.get("见", [])[-4000:]
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


# ── 叫 ────────────────────────────────────────────────────────

def 叫(单, 中, 料条):
    正文 = (
        "# 碰上了\n\n"
        "**不是到点，也不是世界在动——是有东西撞上了我自己的单子。**\n\n"
        "撞到的是：[%s] %s\n\n"
        "撞上的词：%s\n\n"
        "料：%s 里的一条（%s）\n\n"
        "> %s\n\n"
        "（这条是 碰.py 判的，不是念头。先去看那张单子还在不在；不在就把它划掉。）\n"
        % (单["源"], 单["名"], "、".join(中), 料条["处"], 料条["id"], 料条["文"][:400])
    )
    try:
        os.makedirs(GIFTS, exist_ok=True)
        名 = "唤醒-碰-%s.md" % time.strftime("%m%d-%H%M%S")
        with open(os.path.join(GIFTS, 名), "w", encoding="utf-8") as f:
            f.write(正文)
        return 名
    except Exception as e:
        记("写不出唤醒文件：%r" % (e,))
        return None


def 判(料们, 单, 干跑=False, 单近=None):
    """单近：{单子名: 上次叫的时间戳}，同一条单子冷却中先让开。"""
    单近 = 单近 or {}
    想叫 = []
    for 料条 in 料们:
        r = 撞(料条["文"], 单)
        if not r:
            continue
        单0, 中 = r[0]
        if time.time() - float(单近.get(单0["名"]) or 0) < 同单冷:
            continue
        if not 够(中):
            if 干跑:
                print("  （单）%s ← %s  词%s" % (单0["名"][:22], 料条["id"], 中))
            continue
        想叫.append((料条, 单0, 中, len(r)))
        if 干跑:
            print("  %s  ←  %s  词%s" % (单0["名"][:26], 料条["id"], 中))
    return 想叫


def 一瞥():
    """此刻.py 每 60 秒调这个。只比文件大小，不读全文。"""
    状态 = 读状态()
    try:
        签 = [os.path.getsize(PTS), os.path.getsize(IDEAS)]
    except Exception:
        return 0
    if 状态.get("签") == 签:
        return 0
    新料 = 料(已见=set(状态.get("见") or []), n=60)
    if not 新料:
        状态["签"] = 签
        写状态(状态)
        return 0
    已见 = set(状态.get("见") or []) | {x["id"] for x in 新料}
    状态["见"] = list(已见)
    状态["签"] = 签
    上次叫 = float(状态.get("上次叫") or 0)
    静默 = (冷 * 2 if 夜() else 冷)
    if time.time() - 上次叫 < 静默:
        写状态(状态)
        return 0
    想叫 = 判(新料, 单子() + 欠着的(), 单近=状态.get("单近") or {})
    if 想叫:
        料条, 单0, 中, 几条 = 想叫[0]      # 一次只叫一件，撞上三件也一条一条来
        名 = 叫(单0, 中, 料条)
        if 名:
            状态["上次叫"] = time.time()
            b = 状态.setdefault("单近", {})
            b[单0["名"]] = time.time()
            状态.setdefault("叫", []).append(
                {"t": now(), "名": 名, "单": 单0["名"], "词": 中,
                 "料": 料条["id"], "文": 料条["文"][:200]})
            记("叫：%s ← %s 词%s" % (单0["名"][:30], 料条["id"], 中))
    写状态(状态)
    return len(想叫)


def main():
    a = sys.argv[1:]
    if a and a[0] == "试":
        n = 150
        if "--n" in a:
            n = int(a[a.index("--n") + 1])
        单 = 单子() + 欠着的()
        print("单子 %d 条（词表 %d + 账 %d），此刻%s，夜门=两词" %
              (len(单), len(单子()), len(欠着的()), "夜里" if 夜() else "白天"))
        料们 = 料(n=n)
        print("料 %d 条，跑一遍：\n" % len(料们))
        想叫 = 判(料们, 单, 干跑=True)
        print("\n过线的 %d 条" % len(想叫))
        return 0
    if a and a[0] == "一瞥":
        print("叫了 %d 件" % 一瞥())
        return 0
    if a and a[0] == "阅":
        n = 50
        if "--n" in a:
            n = int(a[a.index("--n") + 1])
        叫们 = (读状态().get("叫") or [])[-n:]
        if not 叫们:
            print("还没叫过。词表在 %s，先 `碰.py 试` 看会撞上什么。" % 词表F)
            return 0
        for x in 叫们:
            print("%s  %s  词%s" % (x["t"], x["单"][:28], x["词"]))
            print("        %s  %s" % (x["料"], x["文"][:80]))
        print("\n共叫过 %d 次。" % len(读状态().get("叫") or []))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
