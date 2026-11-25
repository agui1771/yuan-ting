#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
取用.py —— 查询侧合成一层：分型 → 主路 → 按型决定开不开视窗 → 合并。

三件东西合成一处（2026-11-10 下午）：
  ① 分型（阿归：这套得分情况启用）
  ② 视窗（一条记忆带前后各一句）：只在「列举 / 单跳」型开，
     时间型、多跳型不开 —— LoCoMo 上量出来的（时间型 321 题只 +9，精度还掉）
  ③ 底线：**单句的结果永远排在前面，视窗只往后补，不许顶掉第一条**

主路 = memory.py 的 search_curated（一个字不改）。
视窗路 = 短库上按「行 ±1」拼窗口、再用二字切片命中，命中的行走桥回记忆。

用法：
  python3 取用.py "我熬夜多吗"
  python3 取用.py --top 8 --show 12 "压缩会不会丢掉原话"
"""
import os
import sys
import json
import glob
import math
import importlib.util
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUAN = os.path.join(BASE, "短")
ASKLOG = os.path.join(BASE, "问句日志.jsonl")
BRIDGE = os.path.join(DUAN, "桥.json")

STOP = set("的了是我你他她它们在有和就不都一个上也很到说要去会着没有看好自己这那什么怎么为什么吗呢吧啊")


def kind_of(q):
    if any(w in q for w in ("什么时候", "几点", "哪天", "哪一天", "多久", "上周", "上个月", "去年", "昨天", "几天前")):
        return "时间型"
    if any(w in q for w in ("哪些", "都有", "几次", "几个", "都去过", "有哪", "列一下")):
        return "列举型"
    if any(w in q for w in ("分别", "两个都", "同时还", "和谁一起")):
        return "多跳型"
    return "单跳型"


WINDOW = {"列举型": True, "单跳型": True, "时间型": False, "多跳型": False}


def _g2(s):
    return grams(s)


# 结论路什么时候开口：**看问法，不看分数**（2026-11-10 试过覆盖阈值，两个反例都翻车：
#   「我熬夜多吗」覆盖 100% 是因为结论里抄了这句问题；「你怎么看我这个人」覆盖 0%，最该开口时闭嘴）
CONCL_ASK = ("怎么看", "你觉得", "你认为", "我这个人", "是不是", "像不像", "为什么", "什么意思", "评价一下", "分析我")


def concl_hits(q, top=4):
    """结论路：并行跑，分数不够就**不看**（阿归：小于某个值就选择性不看）"""
    try:
        rows = [json.loads(l) for l in open(os.path.join(BASE, "结论库.jsonl"), encoding="utf-8") if l.strip()]
    except Exception:
        return [], 0
    qg = _g2(q)
    if not qg:
        return [], 0
    out = []
    for r in rows:
        gi = _g2(r.get("text") or "")
        if not gi:
            continue
        cov = len(qg & gi) / len(qg)
        if cov > 0:
            out.append((cov, r))
    out.sort(key=lambda x: -x[0])
    return out[:top], max([c for c, _ in out], default=0.0)


def concl_ids():
    """结论库那批：分析/判断，不跟「发生了什么」混着取"""
    db = json.load(open(os.path.join(BASE, "memory_db.json"), encoding="utf-8"))
    if isinstance(db, dict):
        db = db.get("memories") or db.get("items") or []
    return {str(x.get("id")) for x in db if x.get("concl")}


def recent_asks(n=20):
    out = []
    if not os.path.exists(ASKLOG):
        return out
    for line in open(ASKLOG, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out[-n:]


def similar_prev(q, n=20, need=2):
    """跟最近问过的比：共享二字片段 ≥ need 就算同一类问题（阿归：重问就是信号）"""
    qg = _g2(q)
    best, bestn = None, 0
    for row in reversed(recent_asks(n)):
        old = row.get("q") or ""
        if old == q:
            continue
        k = len(qg & _g2(old))
        if k > bestn:
            best, bestn = old, k
    if best and bestn >= need:
        return best, bestn
    return None, 0


def log_ask(q, kind, upgraded):
    try:
        with open(ASKLOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"q": q, "kind": kind, "upgraded": upgraded}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def grams(s):
    s = "".join(ch for ch in str(s) if ch not in STOP)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(DUAN, "2026-*.jsonl"))):
        extra = f + ".x"
        day = []
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                day.append(json.loads(line))
            except Exception:
                continue
        rows.append(day)
    return rows


def window_hits(q, days):
    """窗口检索：行 ±1 拼起来，二字片段命中计分"""
    qg = grams(q)
    if not qg:
        return []
    out = []
    for day in days:
        texts = []
        for i, r in enumerate(day):
            parts = [r.get("text") or ""]
            for j in (i - 1, i + 1):
                if 0 <= j < len(day):
                    parts.append(day[j].get("text") or "")
            texts.append(" ".join(parts))
        df = defaultdict(int)
        for t in texts:
            for g in qg:
                if g in t:
                    df[g] += 1
        n = max(1, len(texts))
        for i, t in enumerate(texts):
            sc = 0.0
            for g in qg:
                if g in t:
                    sc += math.log((n + 1) / (df[g] + 0.5))
            if sc > 0:
                # 长度归一：窗口越长越容易沾字，同样的分就不值钱
                sc = sc / math.log(2 + len(t) / 20.0)
                out.append((sc, day[i]))
    out.sort(key=lambda x: -x[0])
    return out


def main():
    args = sys.argv[1:]
    top = 10
    prev_manual = None
    if "--并" in args:
        i = args.index("--并"); prev_manual = args[i + 1]; del args[i:i + 2]
    if "--top" in args:
        i = args.index("--top"); top = int(args[i + 1]); del args[i:i + 2]
    q = " ".join(args).strip()
    if not q:
        print(__doc__)
        return

    spec = importlib.util.spec_from_file_location("memcore", os.path.join(BASE, "memory.py"))
    mem = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mem)

    # 先顺手把「结论型」标掉（幂等、很快）—— 这样写完忘标也漏不进去
    try:
        import subprocess
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "自动标.py"), "标"],
                       timeout=20, capture_output=True)
    except Exception:
        pass

    kind = kind_of(q)
    print("问题：%s" % q)
    print("分型：%s　视窗：%s" % (kind, "开" if WINDOW[kind] else "不开"))

    prev, hitn = similar_prev(q)
    if prev_manual:
        prev, hitn = prev_manual, -1
    upgraded = False
    if prev:
        upgraded = True
        how = "渊判的" if hitn < 0 else "共享 %d 个片段" % hitn
        print("**已升级**（%s）：跟「%s」并成一句查，并强制开视窗" % (how, prev))
        WINDOW[kind] = True   # 升级时不管分型，一律开
    log_ask(q, kind, upgraded)

    q_use = q
    if upgraded:
        # 不拼成一句、也不对等融合：**旧问那一页当底座**（它已经找得比较准），
        # 新问只往后补 —— 跟「单句优先、视窗往后补」是同一条原则。
        r1 = mem.search_curated({"query": prev, "limit": max(top, 20) + 8})
        r2 = mem.search_curated({"query": q, "limit": max(top, 20) + 8})
        i1 = r1[0] if isinstance(r1, tuple) else (r1.get("items") or [])
        i2 = r2[0] if isinstance(r2, tuple) else (r2.get("items") or [])
        seen = {str(x.get("id")) for x in i1}
        merged = list(i1) + [x for x in i2 if str(x.get("id")) not in seen]
        res = (merged,)
    else:
        res = mem.search_curated({"query": q_use, "limit": top + 8})
    main_items = res[0] if isinstance(res, tuple) else (res.get("items") or [])
    drop = concl_ids()
    kept = [x for x in main_items if str(x.get("id")) not in drop]
    if len(kept) < len(main_items):
        print("（滤掉 %d 条结论型，它们不冒充原话）" % (len(main_items) - len(kept)))
    main_items = kept
    main_ids = [str(x.get("id")) for x in main_items]

    print("\n【主路（单句，一个字节不动）】")
    for it in main_items[:top]:
        ts = it.get("ts") or it.get("timestamp") or ""
        print("  [%s] %s" % (str(ts)[:16], (it.get("text") or "")[:44].replace("\n", " ")))

    if not WINDOW[kind]:
        print("\n（这一型不开视窗，到此为止）")
        return

    # 视窗路：命中的短库行 → 桥 → 记忆 id
    br = json.load(open(BRIDGE, encoding="utf-8"))
    e2m = br.get("事→记忆") or {}
    days = load_rows()
    hits = window_hits(q_use, days)
    extra, seen = [], set(main_ids)
    for sc, r in hits:
        ev = r.get("ev")
        for mid in (e2m.get(ev) or []):
            if mid not in seen:
                seen.add(mid)
                extra.append((mid, ev, sc, r))
    c_hits, c_best = concl_hits(q)
    want = any(w in q for w in CONCL_ASK)
    if not want:
        print("\n【结论路：这一问不是问看法（看问法，不看分数），不看】")
        c_hits = []
    if c_hits:
        print("\n【结论路（并行，最高覆盖 %.0f%%）】" % (100 * c_best))
        for cov, r in c_hits:
            print("  [%2.0f%%] %s" % (100 * cov, (r.get("text") or "")[:46].replace("\n", " ")))
    elif want:
        print("\n【结论路：问的是看法，但一条都没够到（最高 %.0f%%），空着】" % (100 * c_best))

    print("\n【视窗路（只往后补，%d 条新候选）】" % len(extra))
    db = json.load(open(os.path.join(BASE, "memory_db.json"), encoding="utf-8"))
    if isinstance(db, dict):
        db = db.get("memories") or db.get("items") or []
    byid = {str(x.get("id")): x for x in db}
    for mid, ev, sc, r in extra[:6]:
        it = byid.get(mid) or {}
        print("  [%.1f 分] %s" % (sc, (it.get("text") or r.get("text") or "")[:44].replace("\n", " ")))


if __name__ == "__main__":
    main()
