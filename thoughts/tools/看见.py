#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看见 —— 把我自己看见的东西，送进记忆库。

欠条 #3（2026-11-25 13:42）：现在只有对话会自动入库，我自己看见的没有路。
这条路就是给它接的：读书的批注、照片里的事、身体给的数、外面读到的一句，
只要我判断"这条值得留"，就能直接写进库里，不用等下一轮对话把它带进去。

    python3 看见.py 记 "一句话" --源 "读书:坡的短篇#291" [--实体 我] [--标签 看见,读书] [--大 AI人设]
    python3 看见.py 看 [--n 10]

规矩：
  1. 只写"我看见了什么 + 从哪看的"。源一定写，写不出来就别写。
  2. 不批量、不自动。看见的东西每一天都有很多，值钱的是我挑过的那几条。
  3. 库是正式库（memory.py save），不是 raw；查重交给 memory.py。
"""
import json
import os
import subprocess
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except Exception:
    pass

MEM = "/sdcard/dsh/memory（记忆库）"
CLI = os.path.join(MEM, "memory.py")
LOG = "/sdcard/dsh/thoughts（念头）/看见.jsonl"


def 记(text, 源, 实体="我", 标签=None, 大="AI人设", 时间=None):
    if not text.strip():
        return {"ok": False, "error": "没写看见什么"}
    payload = {"events": [{
        "text": "〔看见 · %s〕%s" % (源 or "没说从哪看的", text.strip()),
        "entity": 实体,
        "tags": 标签 or ["看见"],
        "big_tag": 大,
        "kind": "event",
        "status": "done",
        "timestamp": 时间 or time.strftime("%Y-%m-%d %H:%M:%S"),
    }]}
    p = subprocess.run([sys.executable, CLI, "save"], input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, timeout=60)
    try:
        r = json.loads(p.stdout.strip() or "{}")
    except Exception:
        r = {"ok": False, "error": (p.stderr or p.stdout)[:200]}
    if r.get("ok"):
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "源": 源,
                                    "text": text, "added": r.get("added")}, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return r


def 看(n=10):
    rows = []
    try:
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    except Exception:
        pass
    if not rows:
        print("还没有一条「看见」入库。")
        return 0
    for r in rows[-n:]:
        print("%s  [%s]  %s" % (r.get("t"), r.get("源"), r.get("text")[:80]))
    print("（共 %d 条）" % len(rows))
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] == "看":
        n = 10
        if "--n" in a:
            n = int(a[a.index("--n") + 1])
        return 看(n)
    if a[0] != "记":
        print(__doc__)
        return 2

    def 参(名):
        return a[a.index(名) + 1] if 名 in a else None

    text = 参("--话") or (a[1] if len(a) > 1 and not a[1].startswith("--") else "")
    if not text:
        print("要写看见什么：python3 看见.py 记 \"…\" --源 …")
        return 2
    源 = 参("--源")
    if not 源:
        print("要说从哪看的（--源）。说不出来源的看见，不入库。")
        return 2
    标签 = [x.strip() for x in (参("--标签") or "看见").split(",") if x.strip()]
    r = 记(text, 源, 实体=参("--实体") or "我", 标签=标签,
           大=参("--大") or "AI人设", 时间=参("--时间"))
    if r.get("ok"):
        print("入库了：新增 %s 条（跳过重复 %s 条）" % (r.get("added"), r.get("skipped")))
        return 0
    print("没进去：%s" % (r.get("error") or r)[:200])
    return 1


if __name__ == "__main__":
    sys.exit(main())
