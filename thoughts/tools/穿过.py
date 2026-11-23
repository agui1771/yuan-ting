#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""穿过 —— 把一条原产物拿去碰记忆系统，记下落点。

    python3 穿过.py run [--n 19]      把最近 n 条原产物穿过一遍，写落点
    python3 穿过.py show [--n 10]     看落点
    python3 穿过.py one 偏斜-0199     只穿一条

规矩：
  1. 原产物一个字都不改。这里只读，只写 偏斜/落点.jsonl。
  2. 落点 = 碰上的锚点（实体 / 小标签 / 大标签）+ 指回的那条记忆。
  3. 一个都没碰到 → rootless=true。那不是失败，是"我这儿还没有能接住它的地方"。
  4. 同一条可以反复穿，每次一个时间戳，所以原产物 : 落点 = 1 : N。
"""
import importlib.util
import json
import os
import re
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("dianbo", os.path.join(HERE, "电波.py"))
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

F_TABLE = db.F_TABLE
ROOT = os.environ.get("DSH_ROOT", "<DSH_ROOT>")
SELF_TABLE = os.path.join(ROOT, "memory（记忆库）/词表/自创表.json")
MEM_DB = os.path.join(ROOT, "memory（记忆库）/结论库.jsonl")
STREAM = os.path.join(ROOT, "thoughts（念头）/偏斜/流.jsonl")
POINTS = os.path.join(ROOT, "thoughts（念头）/偏斜/落点.jsonl")

# 太泛的锚点，撞上也没信息量
STOP = {"生", "三", "形", "度", "隙", "混沌", "临界", "生数", "万物", "原话", "结论", "待验证"}


def anchors():
    """锚点池：实体 / 小标签 / 大标签 → 最近一条记忆"""
    out = {}
    for line in open(MEM_DB, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        cand = []
        if d.get("entity"):
            cand.append(d["entity"])
        cand += (d.get("small_tags") or [])
        if d.get("big_tag"):
            cand.append(d["big_tag"])
        ts = d.get("timestamp") or ""
        for a in cand:
            a = str(a).strip()
            if len(a) < 2 or a in STOP:
                continue
            cur = out.get(a)
            if cur is None or ts > cur["ts"]:
                out[a] = {"anchor": a, "ts": ts, "id": d.get("id"), "text": (d.get("text") or "")[:40]}
    return out


def load_words():
    t = db.read_json(F_TABLE)
    sym = t["words"]
    try:
        self_t = json.load(open(SELF_TABLE, encoding="utf-8"))
    except Exception:
        self_t = {}
    return sym, self_t


def decoded(rec, sym, self_t):
    out = []
    for s in rec.get("s", ""):
        w = self_t.get(s) or sym.get(s)
        if isinstance(w, (list, tuple)):
            w = w[0] if w else None
        out.append(w if w else "□")
    return out


def hit(words, pool):
    got = {}
    for w in words:
        if not w or w == "□" or len(w) < 2:
            continue
        for a, info in pool.items():
            if a == w or (len(a) >= 3 and a in w) or (len(w) >= 3 and w in a):
                got[a] = info
    return list(got.values())


def do_one(rec, pool, sym, self_t):
    if rec.get("kind") == "空":
        # 空的一口：没有料可穿。记一笔"它空着"，不拿它冒充"无根"。
        return {"src": rec.get("id"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "words": "", "hits": [], "empty": True, "rootless": False}
    ws = decoded(rec, sym, self_t)
    hs = hit(ws, pool)
    return {
        "src": rec.get("id"),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "words": " ".join(ws),
        "hits": hs,
        "rootless": len(hs) == 0,
    }


def run(n=19):
    sym, self_t = load_words()
    pool = anchors()
    recs = [json.loads(l) for l in open(STREAM, encoding="utf-8") if l.strip()]
    todo = recs[-n:]
    with open(POINTS, "a", encoding="utf-8") as f:
        for r in todo:
            p = do_one(r, pool, sym, self_t)
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
            tag = "空" if p.get("empty") else ("无根" if p["rootless"] else "落" + str(len(p["hits"])))
            print(f"{p['src']}  {tag}  {', '.join(h['anchor'] for h in p['hits'])[:60]}")
    print("锚点池", len(pool), "本池条数", len(todo))


def one(src):
    sym, self_t = load_words()
    pool = anchors()
    for line in open(STREAM, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("id") == src:
            p = do_one(r, pool, sym, self_t)
            with open(POINTS, "a", encoding="utf-8") as f:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
            tag = "空" if p.get("empty") else ("无根" if p["rootless"] else "落" + str(len(p["hits"])))
            print(f"{p['src']}  {tag}")
            return
    print("没找到", src)


def show(n=10):
    if not os.path.exists(POINTS):
        print("还没有落点")
        return
    rows = [json.loads(l) for l in open(POINTS, encoding="utf-8") if l.strip()]
    for p in rows[-n:]:
        print(f"--- {p['src']} @ {p['ts']}  {'无根' if p['rootless'] else ''}")
        print("  词:", p["words"][:80])
        for h in p["hits"]:
            print(f"  落: {h['anchor']} → {h['id']} {h['ts']}  「{h['text']}」")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "show"
    n = 19
    if "--n" in a:
        n = int(a[a.index("--n") + 1])
    if cmd == "one":
        one(a[1] if len(a) > 1 else "")
    elif cmd == "run":
        run(n)
    elif cmd == "show":
        show(n)
    else:
        print(__doc__)
