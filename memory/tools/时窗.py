#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""时窗 —— 在任意一个时间区间里，从库里挑东西出来。

2026-11-22 夜里，他定的形制：
  短库不要去动它。分开一个小程序，时间可以自己改，
  想在哪个区间挑就在哪个区间挑 —— 像大标签的开关那样，是个旋钮，不是暗桩。

所以：
  · memory.py 的检索路径一个字不改（近况原来怎么走还怎么走）
  · 这个文件只读，不动库
  · 「压缩点」是一个可以改的默认起点：压缩之后说的话都还在当前窗口里，
    想只看"被压走的那部分"，起点就设成压缩点。

用法
  python3 时窗.py                       # 压缩点 → 现在，短库+长库
  python3 时窗.py --from "2026-11-20 00:00" --to "2026-11-21 00:00"
  python3 时窗.py --src 短 --limit 20
  python3 时窗.py --show-point          # 现在压缩点是几点（连规矩一起打出来）
  python3 时窗.py --set-point "2026-11-22 10:11:22"    # 改它（要写就写这一下）

压缩点是什么：会话日志里【上一次 compaction/end 的真实时刻】。
  不是"下一次压缩大概会发生在几点"，也【不许拿当前钟点填】。
  2026-11-22 夜里我把它当成"压之前"的现在时间填了，早了 22 秒；
  早上填对过一版，晚上却填错 —— 因为「压缩点」在心里有两个意思。
  规矩：宁晚勿早。早了，被压走的那一小段会被当成还在窗口里，近况就漏了。
  真值怎么取：
    zstdcat ~/.dsh/sessions/--root-*/*/session.v4.jsonl.zstd | \
      grep -o '"type":"compaction/end"[^\n]\{0,80\}' | tail -1
  里面的 time 是毫秒 epoch，TZ=Asia/Shanghai date -d @<秒> 换算。
  python3 时窗.py --json                # 给机器看的
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUAN = os.path.join(BASE, "短")
DB = os.path.join(BASE, "memory_db.json")
POINT = os.path.join(DUAN, "压缩点.json")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Shanghai")
except Exception:                                  # 没有 tzdata 就硬退 8 小时
    TZ = timezone(timedelta(hours=8))


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def read_point():
    try:
        with open(POINT, encoding="utf-8") as fh:
            return str(json.load(fh).get("ts") or "").strip()
    except Exception:
        return ""


def write_point(ts):
    old = {}
    try:
        with open(POINT, encoding="utf-8") as fh:
            old = json.load(fh) or {}
    except Exception:
        pass
    old["ts"] = ts
    old["updated"] = now_str()
    old.setdefault("note", "压缩点：这之后的话都还在当前窗口里。每次压缩后手动更新。")
    os.makedirs(DUAN, exist_ok=True)
    tmp = POINT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(old, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, POINT)
    return old


def from_duan(t0, t1, limit):
    out = []
    for f in sorted(glob.glob(os.path.join(DUAN, "20*-*-*.jsonl"))):
        day = os.path.basename(f)[:10]
        if day < t0[:10] or day > t1[:10]:
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    ts = str(r.get("ts") or "")
                    if t0 <= ts <= t1:
                        out.append({"ts": ts, "src": "短",
                                    "who": r.get("role") or "",
                                    "id": r.get("id") or r.get("ev") or "",
                                    "text": str(r.get("text") or "")})
        except Exception:
            continue
    out.sort(key=lambda x: x["ts"])
    return out[-limit:] if limit else out


def from_db(t0, t1, limit):
    try:
        with open(DB, encoding="utf-8") as fh:
            rows = (json.load(fh) or {}).get("memories") or []
    except Exception:
        return []
    out = []
    for r in rows:
        ts = str(r.get("timestamp") or r.get("recorded_at") or "")
        if t0 <= ts <= t1:
            out.append({"ts": ts, "src": "长",
                        "who": (r.get("big_tag") or "") + ("/" + r.get("entity") if r.get("entity") else ""),
                        "id": r.get("id") or "",
                        "text": str(r.get("text") or "")})
    out.sort(key=lambda x: x["ts"])
    return out[-limit:] if limit else out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--from", dest="t0", default="")
    ap.add_argument("--to", dest="t1", default="")
    ap.add_argument("--src", default="both", choices=["短", "长", "both", "duan", "db"])
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--chars", type=int, default=120, help="每条截多少字，0=全文")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-point", action="store_true")
    ap.add_argument("--set-point", default="")
    a = ap.parse_args()

    if a.set_point:
        ts = a.set_point.strip()
        try:
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                ts = datetime.strptime(ts, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                print("时间格式不对，要 YYYY-MM-DD HH:MM 或带秒")
                return 2
        d = write_point(ts)
        print(json.dumps({"ok": True, "point": d}, ensure_ascii=False))
        return 0

    point = read_point()
    if a.show_point:
        print(json.dumps({
            "point": point,
            "now": now_str(),
            "rule": "压缩点 = 上一次 compaction/end 的真实时刻；不许拿当前钟点填。"
                    "宁晚勿早：早了，被压走的那一小段会被当成还在窗口里。",
        }, ensure_ascii=False))
        return 0

    t0 = a.t0 or point or (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    t1 = a.t1 or now_str()
    if len(t0) == 16:
        t0 += ":00"
    if len(t1) == 16:
        t1 += ":59"

    src = {"duan": "短", "db": "长"}.get(a.src, a.src)
    rows = []
    if src in ("短", "both"):
        rows += from_duan(t0, t1, a.limit)
    if src in ("长", "both"):
        rows += from_db(t0, t1, a.limit)
    rows.sort(key=lambda x: x["ts"])

    if a.json:
        print(json.dumps({"from": t0, "to": t1, "count": len(rows), "rows": rows},
                         ensure_ascii=False))
        return 0

    print("[时窗] %s → %s   （共 %d 条）" % (t0, t1, len(rows)))
    for r in rows:
        t = r["text"].replace("\n", " ")
        if a.chars and len(t) > a.chars:
            t = t[:a.chars] + "…"
        print("  %s [%s] %-8s %s" % (r["ts"][5:16], r["src"], r["who"][:8], t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
