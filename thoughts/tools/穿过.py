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
SELF_TABLE = "/sdcard/dsh/memory（记忆库）/词表/自创表.json"
MEM_DB = "/sdcard/dsh/memory（记忆库）/结论库.jsonl"
STREAM = "/sdcard/dsh/thoughts（念头）/偏斜/流.jsonl"
POINTS = "/sdcard/dsh/thoughts（念头）/偏斜/落点.jsonl"

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
    """符号 → 词。两张表都读：origin/表.json（定死的那一代）和 词表/自创表.json（一直在长的那本）。

    2026-11-24 02:35 之前这里只读 origin/表.json，而 自创表.json 只是被 load 进来当空字典用
    （它的键是词、不是符号，`self_t.get(symbol)` 永远取不到）。结果：
      · 表定死之后新加的词根本解不出来，解出来是 □；
      · 只要 自创表.json 里有一个同名键，它就会盖掉 origin 表里那个符号的正词。
    现在两张都读，**符号必须两边都对得上才叫命中**，对不上就还是 □。
    """
    t = db.read_json(F_TABLE)
    SYMBOLS = set(t["words"])
    sym = dict(t["words"])
    try:
        self_t = json.load(open(SELF_TABLE, encoding="utf-8"))
    except Exception:
        self_t = {}
    for k, v in self_t.items():
        name = v.get("符") if isinstance(v, dict) else None
        if not name:
            continue
        if name in SYMBOLS and not sym.get(name):
            sym[name] = k          # 只补 origin 表里空着的那些符号，不覆盖
        elif name in SYMBOLS and sym.get(name) != k:
            raise SystemExit(f"自创表里 {k} 挂的符 {name} 在 origin 表已经是 {sym.get(name)}，"
                             f"两张表对不上。这里不赌，停。")
    return sym, self_t


def decoded(rec, sym, self_t):
    out = []
    for s in rec.get("s", ""):
        w = self_t.get(s) or sym.get(s)
        if isinstance(w, (list, tuple)):
            w = w[0] if w else None
        out.append(w if w else "□")
    return out


# 冷却：最近 COOLDOWN_WINDOW 条落点里已经撞过 COOLDOWN_TIMES 次以上的锚点，这一轮先让开。
# 起因见 FEELINGS.md 前一条与本文件 bak：423 条落点里，有落点的 307 条全部落在同样那五个词上
# （阈值/打翻的牛奶/修静默/踩坑/记忆库分层各 86~98 次），等于没有检索，只有那五句在被反复引用。
COOLDOWN_WINDOW = 30
COOLDOWN_TIMES = 3


def _recent_anchors(n):
    """读落点末尾 n 条的锚点频次。读不着就当没有，不拦。"""
    cnt = {}
    try:
        lines = open(POINTS, encoding="utf-8").read().splitlines()
    except Exception:
        return cnt
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        for h in d.get("hits") or []:
            a = h.get("anchor")
            if a:
                cnt[a] = cnt.get(a, 0) + 1
    return cnt


def hit(words, pool, cool=frozenset()):
    got = {}
    for w in words:
        if not w or w == "□" or len(w) < 2:
            continue
        for a, info in pool.items():
            if a in cool:
                continue
            if a == w or (len(a) >= 3 and a in w) or (len(w) >= 3 and w in a):
                got[a] = info
    return list(got.values())


def _memories():
    """第 3 代撞的对象：真库的正文（memory_db.json），按时间从新到旧。"""
    d = json.load(open("/sdcard/dsh/memory（记忆库）/memory_db.json", encoding="utf-8"))
    ms = d.get("memories") or []
    ms = [m for m in ms if (m.get("text") or "").strip()]
    ms.sort(key=lambda m: str(m.get("timestamp") or ""), reverse=True)
    return ms


_MEMS = [None]


def hit_doc(words, cap=3, df_max=3):
    """一个词撞进正文里。**窄的才算**：出现在太多条记忆里的词是路过，不是相撞。

    2026-11-24 实测：2848 个词里 1512 个（53%）在整个库里只出现在 1 条记忆里；
    171 个出现在 10 条以上（压缩/记忆/手机/判断…）。后者撞上等于没撞。
    """
    if _MEMS[0] is None:
        try:
            _MEMS[0] = _memories()
        except Exception:
            _MEMS[0] = []
    got = {}
    for w in words:
        if not w or w == "□" or len(w) < 2:
            continue
        where = [m for m in _MEMS[0] if w in (m.get("text") or "")]
        if not where or len(where) > df_max:
            continue
        best = where[0]          # _MEMS 已按时间从新到旧
        got[w] = {"anchor": w, "id": best.get("id"),
                  "ts": str(best.get("timestamp") or "")[:19],
                  "text": (best.get("text") or "")[:40],
                  "entity": best.get("entity"), "df": len(where)}
    # 越窄越好；同样窄的，词长的优先（短词多半是常用词）
    hs = sorted(got.values(), key=lambda h: (h["df"], -len(h["anchor"])))
    return hs[:cap]


def do_one(rec, pool, sym, self_t, cool=frozenset()):
    if rec.get("kind") == "空":
        # 空的一口：没有料可穿。记一笔"它空着"，不拿它冒充"无根"。
        return {"src": rec.get("id"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "words": "", "hits": [], "empty": True, "rootless": False}
    if int(rec.get("gen", 1)) >= 3:
        ws = rec.get("w") or []
        hs = hit_doc(ws)
        return {"src": rec.get("id"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "gen": 3, "words": " ".join(ws), "hits": hs,
                "rootless": len(hs) == 0}
    ws = decoded(rec, sym, self_t)
    hs = hit(ws, pool, cool)
    return {
        "src": rec.get("id"),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "words": " ".join(ws),
        "hits": hs,
        "rootless": len(hs) == 0,
    }


    if rec.get("kind") == "空":
        # 空的一口：没有料可穿。记一笔"它空着"，不拿它冒充"无根"。
        return {"src": rec.get("id"), "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "words": "", "hits": [], "empty": True, "rootless": False}
    ws = decoded(rec, sym, self_t)
    hs = hit(ws, pool, cool)
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
    recent = _recent_anchors(COOLDOWN_WINDOW)
    cool = frozenset(a for a, c in recent.items() if c >= COOLDOWN_TIMES)
    # 已经落过的 src 不再落第二次。`run --n 19` 的语义是"最近 19 条"，
    # 但每跑一次就多落 19 条重复，481 条里只有 425 条是不同的话——数字是重复撑大的。
    done = set()
    try:
        for line in open(POINTS, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line).get("src"))
                except Exception:
                    pass
    except Exception:
        pass
    todo = [r for r in recs if r.get("id") not in done][-n:]
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
    cool = frozenset(a for a, c in _recent_anchors(COOLDOWN_WINDOW).items() if c >= COOLDOWN_TIMES)
    for line in open(STREAM, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("id") == src:
            p = do_one(r, pool, sym, self_t, cool)
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
