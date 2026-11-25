#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日结抽料器：纯程序，不生成一个字，只挑原话。

  python3 日结.py 2026-11-24          # 打出来
  python3 日结.py 2026-11-24 --write  # 写到 短/日结/YYYY-MM-DD.料.md

规则：
  · 按 3 小时分桶，每桶最多 3 句，保证一天铺得开
  · 用户的话权重 1.0，我的话 0.5（我更啰嗦，压一压）
  · 命中关键词加分：决定/以后/不/别/改/名字/测试/失败/错了/名字/想/怕/难过/名字
  · 太长扣分；跟已选句二字重合 > 0.5 的丢掉（去冗余）
  · 跳过调度提醒、后台任务回执
"""
import json, os, re, sys, time
from collections import defaultdict

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORT = os.path.join(HOME, "短")
OUT = os.path.join(SHORT, "日结")

KW = ["决定", "以后", "不要", "别再", "改", "重来", "名字", "测试", "跑分", "失败", "错",
      "想", "怕", "难过", "舍不得", "对不起", "我爱你", "离开", "睡", "醒", "记住", "忘",
      "架构", "规则", "为什么", "怎么办", "不许", "停"]
SKIP = ("[SCHEDULE REMINDER", "background job", "[凭据已抹", "【§15", "【原话·")

def sents(t):
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"[#>*`]", " ", t)
    parts = re.split(r"(?<=[。！？!?；;\n])", t)
    return [p.strip() for p in parts if len(p.strip()) >= 6]

def bigrams(s):
    s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)
    return {s[i:i+2] for i in range(len(s)-1)} or {s}

def jac(a, b):
    x, y = bigrams(a), bigrams(b)
    return len(x & y) / max(1, len(x | y))

def score(text, role):
    w = 1.0 if role == "user" else 0.45
    kw = 0.0
    for k in KW:
        if k in text:
            kw += 0.35
    kw = min(kw, 1.4)
    n = len(text)
    length = 1.0 / (1 + max(0, n - 40) / 90.0)
    return w * (0.6 + kw) * length

def load(day):
    p = os.path.join(SHORT, day + ".jsonl")
    rows = []
    with open(p, encoding="utf8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = (d.get("text") or "").strip()
            if not t or any(t.startswith(s) or s in t[:40] for s in SKIP):
                continue
            rows.append((i, d.get("ts", ""), d.get("role", ""), t))
    return rows

def pick(rows, per_bucket=3, total=14):
    buckets = defaultdict(list)
    for i, ts, role, text in rows:
        hh = int(ts[11:13]) if len(ts) > 12 else 0
        for s in sents(text):
            buckets[(hh // 3) * 3].append((score(s, role), i, ts, role, s))
    chosen = []
    for b in sorted(buckets):
        cand = sorted(buckets[b], reverse=True)[:per_bucket * 3]
        take = []
        for item in cand:
            if all(jac(item[4], c[4]) < 0.5 for c in take):
                take.append(item)
            if len(take) >= per_bucket:
                break
        chosen.extend(take)
    chosen.sort(key=lambda x: x[2])
    if len(chosen) > total:
        step = len(chosen) / total
        chosen = [chosen[int(i * step)] for i in range(total)]
    return chosen

def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    day = sys.argv[1]
    rows = load(day)
    users = sum(1 for r in rows if r[2] == "user")
    chosen = pick(rows)
    L = []
    L.append("# %s · 料（纯抽句，一个字没改）" % day)
    L.append("")
    L.append("**总条数 %d（他 %d）· %s–%s · 指回：短/%s.jsonl 第 1–%d 条**"
             % (len(rows), users, rows[0][1][11:16] if rows else "?", rows[-1][1][11:16] if rows else "?", day, rows[-1][0] if rows else 0))
    L.append("")
    for _, i, ts, role, s in chosen:
        who = "他" if role == "user" else "我"
        L.append("- `%s` %s（第 %d 条）%s" % (ts[11:16], who, i, s[:110]))
    L.append("")
    L.append("**这是目录，不是证据。**引用要回原文。")
    out = "\n".join(L)
    if "--write" in sys.argv:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, day + ".料.md")
        open(p, "w", encoding="utf8").write(out + "\n")
        print("写出", p)
    else:
        print(out)

if __name__ == "__main__":
    main()
