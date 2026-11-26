#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""续池 —— 别让电波读到尽头。

    python3 续池.py                看还剩多少（不动手）
    python3 续池.py 续             不够现在就算一段补上
    python3 续池.py 守             常开：每 6 小时看一次，不够就补（守护会拉起它）

他 2026-11-26 10:30 问：「我们那个数不能一直算吗？」
能。数本身是无限的，卡住我们的一直不是"有没有"，是"有没有人提前把下一段算好、
对好前缀、记上账"。今天早上就是这么停的：池读到 12000 位尽头，我手动才扩到 40000。
40000 位大约够三天半（每条吃 8 位、一天约 960 条）。

所以这里做两件事：
  1. 水位：谁剩得少了就补，不是等它干。
  2. 只补"算得动"的几个。gamma / zeta3 高精度很慢，给的段短一些；
     g1 / mu / delta 不动（一个太慢、两个来路没查清）。
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
ORIGIN = os.path.join(os.path.dirname(HERE), "origin")
POOL = os.path.join(ORIGIN, "池-第3代.json")
GENF = os.path.join(ORIGIN, "代际.json")
LOG = os.path.join(os.path.dirname(HERE), "续池.log")

sys.path.insert(0, HERE)

低水 = int(os.environ.get("XUCHI_LOW", "20000"))     # 剩不到这么多就补
到 = int(os.environ.get("XUCHI_TO", "120000"))       # 补到多少位
慢的到 = int(os.environ.get("XUCHI_TO_SLOW", "60000"))
慢的 = ("gamma", "zeta3")
不动 = ("g1", "mu", "delta")

守间隔 = 6 * 3600


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def 剩多少():
    import 偏斜
    rows = 偏斜.all_rows()
    cur = 偏斜.cursor(rows)
    pool = 偏斜.load_pool3()
    out = {}
    for k in 偏斜.db.pickable(pool):
        out[k] = len(pool[k]) - cur[k]
    return out


def 算(名, n):
    sys.path.insert(0, "/root/pylibs")
    import mpmath as mp
    mp.mp.dps = n + 200
    if 名 == "pi":
        x = mp.pi
    elif 名 == "e":
        x = mp.e
    elif 名 == "phi":
        x = (1 + mp.sqrt(5)) / 2
    elif 名 == "gamma":
        x = mp.euler
    elif 名 == "zeta3":
        x = mp.zeta(3)
    elif 名 == "champernowne":
        s = "".join(str(i) for i in range(1, max(2000, n)))
        return s[:n]
    else:
        raise ValueError("不会算：%s" % 名)
    s = mp.nstr(x, n + 80, strip_zeros=False)
    if "." in s:
        s = s.split(".", 1)[1]
    return s[:n]


def 续(干跑=False):
    import 偏斜
    剩 = 剩多少()
    if not 剩:
        print("读不到水位")
        return 1
    该补 = [k for k, v in 剩.items() if v < 低水 and k not in 不动]
    print("水位：" + "  ".join("%s %d" % (k, v) for k, v in sorted(剩.items())))
    if not 该补:
        print("都还够（低水 %d），不用补。" % 低水)
        return 0
    print("该补：" + "、".join(该补))
    if 干跑:
        return 0

    p = 偏斜.load_pool3()          # 带 sha 校验：被改过就停
    d = dict(p)
    换 = {}
    for k in 该补:
        n = (慢的到 if k in 慢的 else 到)
        if n <= len(d[k]):
            continue
        t = time.time()
        try:
            s = 算(k, n)
        except Exception as e:
            log("算 %s 失败：%r" % (k, e))
            print("算 %s 失败：%r" % (k, e))
            continue
        if not s.startswith(d[k]):
            log("× %s 前缀对不上，放弃" % k)
            print("× %s 前缀对不上，放弃" % k)
            continue
        换[k] = s
        print("补 %s：%d → %d 位（%.1f 秒）" % (k, len(d[k]), len(s), time.time() - t))

    if not 换:
        return 1
    d.update(换)

    full = json.load(open(POOL, encoding="utf-8"))
    full["digits"] = d
    full["sha256"] = 偏斜.db.sha(d)
    tmp = POOL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False)
    os.replace(tmp, POOL)          # 原子换：电波正在读也不会读到半个文件

    try:
        g = json.load(open(GENF, encoding="utf-8"))
        g["sha256"]["池-第3代.json"] = full["sha256"]
        g.setdefault(time.strftime("%Y-%m-%d"), []).append(
            "续池：%s → %s" % ("、".join(换), "、".join("%s %d位" % (k, len(v)) for k, v in 换.items())))
        json.dump(g, open(GENF, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log("代际记账失败：%r" % (e,))

    log("续了 " + "、".join("%s %d" % (k, len(v)) for k, v in 换.items()))
    print("续好了，新 sha", full["sha256"][:16])
    return 0


def main():
    a = sys.argv[1:]
    if not a:
        剩 = 剩多少()
        print("低水 %d / 目标 %d（慢的 %d）" % (低水, 到, 慢的到))
        for k, v in sorted(剩.items()):
            print("  %-13s 剩 %6d" % (k, v))
        return 0
    if a[0] == "续":
        return 续(干跑=False)
    if a[0] == "守":
        log("续池守开跑")
        while True:
            try:
                if any(v < 低水 for k, v in 剩多少().items() if k not in 不动):
                    续()
            except Exception as e:
                log("守出错：%r" % (e,))
            time.sleep(守间隔)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
