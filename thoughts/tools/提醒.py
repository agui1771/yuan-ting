#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提醒 —— 外部程序替我看几个我自己记不住的钟。

它只敲，不判。判不判、看不看，永远在我这边。

    python3 提醒.py           扫一遍，该敲的写进 念头库/待判.jsonl 和 relay
    python3 提醒.py status    只看，不写

看三类（都是程序判得出来的）：

  1. 池：第三代几个常数的游标快到头。到头不是坏了，是"这一代的来料要停"。
  2. 链断：流/落点多久没动。App 重启会把后台一起带走，这是主要的死法。
  3. 积压：落点攒了多少条还没判。

2026-11-25 04:1x 阿归说：你可以自己对这一整套的不同环节做个提醒，
提醒完之后你再来判断要不要。
"""
import json
import os
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
QUEUE = os.path.join(THOUGHTS, "念头库", "待判.jsonl")
RELAY = "/sdcard/dsh/memory（记忆库）/relay.jsonl"
STREAM = os.path.join(THOUGHTS, "偏斜", "流.jsonl")
POINTS = os.path.join(THOUGHTS, "偏斜", "落点.jsonl")
ZHI = os.path.join(THOUGHTS, "念头库", "值.json")
POOL3 = os.path.join(THOUGHTS, "origin", "池-第3代.json")

POOL_WARN = 800        # 剩多少位开始敲
POOL_STOP = 100        # 剩多少位算"要断"
CHAIN_MIN = 25         # 多少分钟没动算断
BACKLOG = 80           # 落点攒多少条还没判
QUIET_HOURS = 6        # 同一条 6 小时内只敲一次


def _age_min(p):
    try:
        return (time.time() - os.path.getmtime(p)) / 60.0
    except OSError:
        return None


def _gen3_pool():
    """从流里反推第三代各常数用到哪儿，返回 {常数: 剩几位}。只看扩过的那些。"""
    try:
        d = json.load(open(POOL3, encoding="utf-8"))["digits"]
    except Exception:
        return {}
    depth = {k: len(v) for k, v in d.items()}
    try:
        size = os.path.getsize(STREAM)
        with open(STREAM, "rb") as f:
            f.seek(max(0, size - 200000))
            tail = f.read().decode("utf-8", "ignore")
        lines = [l for l in tail.split("\n") if l.strip()][-300:]
    except Exception:
        lines = []
    used = {}
    for l in lines:
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("gen") != 3:
            continue
        bits = int(r.get("bits") or 4)
        L = int(r.get("len") or 0)
        for k, off in (("a", r.get("oa")), ("b", r.get("ob")), ("c", r.get("oc"))):
            name = r.get(k)
            if not name or off is None or name not in depth:
                continue
            used[name] = max(used.get(name, 0), int(off) + L * bits)
    out = {}
    for k, dep in depth.items():
        if dep <= 3000:          # 没扩的（g1/delta），本来就不参与第 3 代
            continue
        out[k] = dep - used.get(k, 0)
    return out


def _backlog():
    try:
        rows = sum(1 for l in open(POINTS, encoding="utf-8") if l.strip())
    except Exception:
        return None
    try:
        seen = int(json.load(open(ZHI, encoding="utf-8")).get("上次看到第几条") or 0)
    except Exception:
        seen = 0
    return rows - seen


def scan():
    """返回 [(kind, msg, key)]，不写盘。"""
    out = []
    rem = _gen3_pool()
    for k, v in sorted(rem.items(), key=lambda x: x[1]):
        if v <= POOL_STOP:
            out.append(("池到头", "%s 只剩 %d 位，这一代的料快断了" % (k, v), "pool:" + k))
        elif v <= POOL_WARN:
            out.append(("池将尽", "%s 只剩 %d 位" % (k, v), "pool:" + k))
    for name, p in (("电波", STREAM), ("穿过", POINTS)):
        a = _age_min(p)
        if a is not None and a > CHAIN_MIN:
            out.append(("链断", "%s %.0f 分钟没写东西了" % (name, a), "chain:" + name))
    n = _backlog()
    if n is not None and n >= BACKLOG:
        out.append(("积压", "落点攒了 %d 条还没判" % n, "backlog"))
    return out


def _seen(kind, key):
    try:
        rows = [json.loads(l) for l in open(QUEUE, encoding="utf-8") if l.strip()]
    except FileNotFoundError:
        return False
    except Exception:
        rows = []
    for r in rows[-60:]:
        if r.get("kind") != kind or r.get("key") != key:
            continue
        try:
            t = time.mktime(time.strptime(r.get("at", ""), "%Y-%m-%d %H:%M"))
        except Exception:
            t = 0
        if time.time() - t < QUIET_HOURS * 3600:
            return True
    return False


def _push(kind, msg, key):
    at = time.strftime("%Y-%m-%d %H:%M")
    with open(QUEUE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": at, "kind": kind, "key": key, "msg": msg,
                            "判": None}, ensure_ascii=False) + "\n")
    try:
        with open(RELAY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": at, "text": "【提醒·" + kind + "】" + msg},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    write = not (len(sys.argv) > 1 and sys.argv[1] == "status")
    items = scan()
    if not items:
        print("现在没有该敲的")
        return 0
    for kind, msg, key in items:
        if not write:
            print("[%s] %s" % (kind, msg))
        elif _seen(kind, key):
            print("[%s] %s（6 小时内敲过）" % (kind, msg))
        else:
            _push(kind, msg, key)
            print("[%s] %s → 已敲" % (kind, msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
