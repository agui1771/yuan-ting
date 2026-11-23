#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""穿过后台 —— 盯着 流.jsonl，来一条就穿一条，只写文件，不进对话。

    python3 穿过后台.py           （常开）
    python3 穿过后台.py --once    只跑一轮

它不叫醒任何人、不写记忆库、不改原产物。
已经穿过的不重复穿（按 src 查 落点.jsonl）。
"""
import json
import os
import subprocess
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("DSH_ROOT", "/sdcard/dsh")
STREAM = os.path.join(ROOT, "thoughts（念头）/偏斜/流.jsonl")
POINTS = os.path.join(ROOT, "thoughts（念头）/偏斜/落点.jsonl")
LOG = os.path.join(ROOT, "thoughts（念头）/穿过.log")
EVERY = 60


def done_ids():
    ids = set()
    if os.path.exists(POINTS):
        for line in open(POINTS, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["src"])
            except Exception:
                pass
    return ids


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def tick():
    if not os.path.exists(STREAM):
        return 0
    seen = done_ids()
    todo = []
    for line in open(STREAM, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("id") and r["id"] not in seen:
            todo.append(r["id"])
    if not todo:
        return 0
    # 一条一条穿，用 穿过.py 的同一套逻辑
    n = 0
    for rid in todo:
        subprocess.run([sys.executable, os.path.join(HERE, "穿过.py"), "one", rid],
                       capture_output=True, timeout=120)
        n += 1
    log(f"穿了 {n} 条：{', '.join(todo[:5])}")
    return n


if __name__ == "__main__":
    if "--once" in sys.argv:
        print("穿了", tick(), "条")
        sys.exit(0)
    log("开始盯流")
    while True:
        try:
            tick()
        except Exception as e:
            log("出错 " + str(e)[:80])
        time.sleep(EVERY)
