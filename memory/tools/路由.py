#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
路由.py —— 问题进来先分型，再决定走哪条路。

他 2026-11-10 定的两级：
  · 问题里有库里能直接命中的词（标签/实体）→ 走窄门，直查，不碰场景那套
  · 窄门空掉（模糊描述）→ 才降级：近义扩展 → 候选池 → **由渊判一遍**才算命中

分型用规则，不用模型。四型：
  时间型 / 列举型 / 描述型（模糊）/ 事实型（默认）

用法：
  python3 路由.py "我上次什么时候说的高敏感"
  python3 路由.py --top 8 "那个咧嘴伸手臂的动物"
"""
import os
import sys
import json
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "memory_db.json")
SYN = os.path.join(BASE, "synonyms.json")
VOCAB = os.path.join(BASE, "词表")

# ---------- 分型 ----------
PAT = {
    "时间型": re.compile(r"什么时候|几点|哪天|哪一天|多久|上周|上个月|去年|昨天|前天|几天前|多少天"),
    "列举型": re.compile(r"哪些|都有|几次|几个|多少件|都去过|列一?下|有哪"),
    "描述型": re.compile(r"那个|那个东西|像|差不多|类似|长得|形容|大概是什么"),
}


def kind_of(q):
    hits = [k for k, p in PAT.items() if p.search(q)]
    return hits or ["事实型"]


# ---------- 库 ----------
def load_db():
    d = json.load(open(DB, encoding="utf-8"))
    if isinstance(d, dict):
        d = d.get("memories") or d.get("items") or []
    return d


def load_syn():
    try:
        d = json.load(open(SYN, encoding="utf-8"))
        return [g for g in d.get("groups", []) if g]
    except Exception:
        return []


def load_words():
    """所有标准词（标签+实体都会在里面）"""
    words = set()
    for f in ("标准词表.json", "小词表.json", "同义表.json"):
        p = os.path.join(VOCAB, f)
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, list):
            words |= {str(x) for x in d}
        elif isinstance(d, dict):
            for v in d.values():
                if isinstance(v, list):
                    words |= {str(x) for x in v if isinstance(x, str)}
                elif isinstance(v, str):
                    words.add(v)
    return words


def index(db):
    """词 -> 条目下标。标签、实体都进。"""
    inv = {}
    for i, it in enumerate(db):
        keys = set()
        for t in (it.get("small_tags") or []):
            keys.add(str(t))
        if it.get("entity"):
            keys.add(str(it["entity"]))
        if it.get("big_tag"):
            keys.add(str(it["big_tag"]))
        for k in keys:
            inv.setdefault(k, []).append(i)
    return inv


# ---------- 走 ----------
def narrow(q, db, inv, words):
    """窄门：问题里出现的、库里真有的词（长词优先）"""
    got = []
    for w in sorted(words, key=len, reverse=True):
        if len(w) >= 2 and w in q and w in inv:
            if any(w in g for g in got):
                continue
            got.append(w)
    ids = set()
    for w in got:
        ids |= set(inv[w])
    # 稳健性：只命中一个字面词不算数（2026-11-10 试出来「敏感」命中了错的条目）
    # 要么命中 ≥2 个词，要么至少有一个词是某条的 entity（主体，不是随手挂的标签）
    strong = False
    if len(got) >= 2:
        strong = True
    else:
        for w in got:
            for i in inv[w]:
                if str(db[i].get("entity") or "") == w:
                    strong = True
                    break
    if not strong:
        return [], set(), got
    return got, ids, []


def widen(q, syn):
    """近义：只在窄门空掉时用。返回 (组内词, 说明)"""
    out = []
    for g in syn:
        hit = [w for w in g if w and w in q]
        if hit:
            out.append((hit[0], g))
    return out


def main():
    args = [a for a in sys.argv[1:]]
    top = 5
    if "--top" in args:
        i = args.index("--top")
        top = int(args[i + 1])
        del args[i:i + 2]
    q = " ".join(args).strip()
    if not q:
        print(__doc__)
        return

    db = load_db()
    inv = index(db)
    words = load_words()

    print("问题：%s" % q)
    print("分型：%s" % "／".join(kind_of(q)))

    got, ids, weak = narrow(q, db, inv, words)
    if ids:
        print("走路：窄门（直查）")
        print("命中词：%s" % "、".join(got))
        print("命中 %d 条（库共 %d）" % (len(ids), len(db)))
        for i in list(ids)[:top]:
            it = db[i]
            print("  [%s] %s | %s" % (it.get("timestamp", "")[:16], it.get("entity") or "",
                                      (it.get("text") or "")[:46].replace("\n", " ")))
        return

    if weak:
        print("窄门只擦到边：%s（不算命中，转降级）" % "、".join(weak))
    print("走路：降级（窄门空）")
    syn = load_syn()
    w = widen(q, syn)
    if not w:
        print("近义也没扩出来 → 该走场景/拼图那套")
        return
    print("近义候选（**要渊判**，判过才算命中）：")
    for base, g in w:
        print("  「%s」这组：%s" % (base, "、".join(g)))
    cand = set()
    for base, g in w:
        for x in g:
            cand |= set(inv.get(x, []))
    print("候选 %d 条（未判）" % len(cand))
    for i in list(cand)[:top]:
        it = db[i]
        print("  [%s] %s | %s" % (it.get("timestamp", "")[:16], it.get("entity") or "",
                                  (it.get("text") or "")[:46].replace("\n", " ")))


if __name__ == "__main__":
    main()
