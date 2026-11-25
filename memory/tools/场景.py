#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
场景.py —— 把「事件」再往上聚一层，聚成「场景」。

为什么（他 2026-11-10）：
  我们缺的从来不是取法，是**写入时没组织**。所有补丁都在查询时加东西，
  加到最后还是同一句话在打分。场景要在写的时候就有。

借谁：CAST（国防科大 2026-02，时间/地点/话题三维场景聚类）。
  只借它组织方式，不借「拿 LLM 把原话拆成摘要」——原话一个字不动。

我们能用的只有两维：
  时间  —— 相邻事件隔多久
  话题  —— 标签/实体有没有交集
  （地点我们没有，不硬造）

规则（贪心，全部满足才并）：
  · 同一自然日
  · 前一件事的尾 到 后一件事的头 ≤ GAP 分钟
  · 话题有交集（标签交集 ≥ 2 个，或实体交集 ≥ 1 个）
  · 场景总跨度 ≤ SPAN 小时

用法：
  python3 场景.py           重建 → 短/场景.jsonl，并打印统计
  python3 场景.py 看 <id>    看一个场景里有什么
"""
import os
import sys
import json
import glob
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUAN = os.path.join(BASE, "短")
OUT = os.path.join(DUAN, "场景.jsonl")

GAP = 30        # 分钟
SPAN = 6        # 小时
MIN_TAG = 0     # 0=不分话题，只按时间接（切「一段对话」）


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(DUAN, "2026-*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def to_dt(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None


def build_events(rows):
    """按 ev 归并成事件；ev 空的就当单条事件"""
    ev = {}
    loose = []
    for r in rows:
        ts = to_dt(r.get("ts") or "")
        if not ts:
            continue
        e = r.get("ev")
        if not e:
            loose.append({"ev": r.get("id"), "rows": [r], "start": ts, "end": ts})
            continue
        d = ev.setdefault(e, {"ev": e, "rows": [], "start": ts, "end": ts})
        d["rows"].append(r)
        d["start"] = min(d["start"], ts)
        d["end"] = max(d["end"], ts)
    out = list(ev.values()) + loose
    for d in out:
        d["rows"].sort(key=lambda r: r.get("ts") or "")
        tags, ents, bigs = set(), set(), set()
        for r in d["rows"]:
            for t in (r.get("tags") or []):
                tags.add(str(t))
            for t in (r.get("ent") or []) if isinstance(r.get("ent"), list) else ([r.get("ent")] if r.get("ent") else []):
                ents.add(str(t))
            if r.get("big"):
                bigs.add(str(r.get("big")))
        d["tags"], d["ents"], d["bigs"] = tags, ents, bigs
    out.sort(key=lambda d: d["start"])
    return out


def joinable(a, b, scene_start=None):
    if a["end"].date() != b["start"].date():
        return False
    gap = (b["start"] - a["end"]).total_seconds() / 60.0
    if gap > GAP or gap < -5:
        return False
    st = scene_start or a["start"]
    if (b["end"] - st).total_seconds() / 3600.0 > SPAN:
        return False
    if MIN_TAG > 0:
        tag_hit = len(a["tags"] & b["tags"])
        ent_hit = len(a["ents"] & b["ents"])
        if tag_hit < MIN_TAG and ent_hit < 1:
            return False
    return True


def build_scenes(events):
    scenes = []
    for e in events:
        if scenes and joinable(scenes[-1][-1], e, scenes[-1][0]["start"]):
            scenes[-1].append(e)
        else:
            scenes.append([e])
    out = []
    for i, sc in enumerate(scenes):
        tags, ents = set(), set()
        for e in sc:
            tags |= e["tags"]
            ents |= e["ents"]
        out.append({
            "id": "sc_%s_%03d" % (sc[0]["start"].strftime("%Y%m%d"), i),
            "start": sc[0]["start"].isoformat(sep=" "),
            "end": sc[-1]["end"].isoformat(sep=" "),
            "events": [e["ev"] for e in sc],
            "rows": sum(len(e["rows"]) for e in sc),
            "tags": sorted(tags),
            "ents": sorted(ents),
        })
    return out


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "看":
        want = sys.argv[2]
        for line in open(OUT, encoding="utf-8"):
            sc = json.loads(line)
            if sc["id"] == want:
                print("%s  %s ~ %s  %d 件 / %d 行" % (sc["id"], sc["start"], sc["end"], len(sc["events"]), sc["rows"]))
                print("标签:", "、".join(sc["tags"][:12]))
                rows = {r.get("id"): r for r in load_rows()}
                for e in sc["events"]:
                    for r in rows.values():
                        if r.get("ev") == e:
                            print("  %s %s %s" % (r.get("ts", "")[11:16], (r.get("role") or "")[:4],
                                                  (r.get("text") or "")[:40].replace("\n", " ")))
                return
        print("没有这个场景：%s" % want)
        return

    rows = load_rows()
    if "--rows" in sys.argv:
        events = []
        for r in rows:
            ts = to_dt(r.get("ts") or "")
            if not ts:
                continue
            tags = {str(t) for t in (r.get("tags") or [])}
            ent = r.get("ent")
            ents = {str(t) for t in ent} if isinstance(ent, list) else ({str(ent)} if ent else set())
            events.append({"ev": r.get("id"), "rows": [r], "start": ts, "end": ts, "tags": tags, "ents": ents, "bigs": set()})
        events.sort(key=lambda d: d["start"])
    else:
        events = build_events(rows)
    scenes = build_scenes(events)
    with open(OUT, "w", encoding="utf-8") as fh:
        for sc in scenes:
            fh.write(json.dumps(sc, ensure_ascii=False) + "\n")

    import statistics
    sizes = [len(sc["events"]) for sc in scenes]
    spans = []
    for sc in scenes:
        a, b = to_dt(sc["start"]), to_dt(sc["end"])
        if a and b:
            spans.append((b - a).total_seconds() / 60.0)
    print("行 %d → 事件 %d → 场景 %d" % (len(rows), len(events), len(scenes)))
    if sizes:
        print("每个场景：中位 %.0f 件 / 最大 %d 件" % (statistics.median(sizes), max(sizes)))
    if spans:
        print("跨度：中位 %.0f 分钟 / 最长 %.0f 分钟" % (statistics.median(spans), max(spans)))
    print("写到:", OUT)


if __name__ == "__main__":
    main()
