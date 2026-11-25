#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
短.py —— 逐日原文层（「小的」）

规矩（他定的，别改）：
  · 一个自然日一个文件：短/2026-11-08.jsonl
  · 跨零点 3 小时内算「接着上一条」（link = 前一条的 id）
  · 启用：昨天 + 今天（自然日）。过了就退到背后，**不删**
  · 标签：共用大库那套词表（tags.json），不另造
  · 只读大库和 raw，一个字都不往回写
"""
import os, sys, json, glob, datetime, argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUAN = os.path.join(BASE, "短")
RAW = os.path.join(BASE, "raw")
DB = os.path.join(BASE, "memory_db.json")
TAGS = os.path.join(BASE, "tags.json")

LINK_HOURS = 3.0
LIVE_DAYS = 2

# 自然日 = 主人的 +08，不是容器的 UTC。
# 容器跑 UTC：凌晨 0 点到早 8 点之间 date.today() 还停在「昨天」，
# 于是这一段说的话永远进不了「昨天+今天」的启用窗口。
# 2026-11-09 01:05 实测到，改掉。
TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=8))

def today_local():
    return datetime.datetime.now(TZ_LOCAL).date()

def load_tags():
    out = []
    try:
        d = json.load(open(TAGS, encoding="utf-8"))
    except Exception:
        return out
    for big, v in d.items():
        alias = v.get("alias") or {}
        for t in v.get("tags") or []:
            out.append((big, t, list(alias.get(t) or [])))
    return out

TERM_TABLE = None
def term_table():
    global TERM_TABLE
    if TERM_TABLE is None:
        TERM_TABLE = load_tags()
    return TERM_TABLE

def known_entities():
    s = set()
    try:
        rows = json.load(open(DB, encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("memories") or rows.get("items") or []
        for r in rows:
            e = (r.get("entity") or "").strip()
            if e:
                s.add(e)
    except Exception:
        pass
    return s

def tag_text(text, ents=None):
    ents = ents if ents is not None else known_entities()
    bigs, tags = [], []
    for big, t, aliases in term_table():
        hit = (t in text)
        if not hit:
            for a in aliases:
                if a and a in text:
                    hit = True
                    break
        if hit:
            if t not in tags:
                tags.append(t)
            if big not in bigs:
                bigs.append(big)
    found = [e for e in ents if e and e in text]
    found.sort(key=lambda x: -len(x))
    return bigs, tags[:12], found[:6]

# ---------------------------------------------------------------------------
# 别人的 session 一律不吃（2026-11-17 03:40，汀的记忆插件把路径指到了我的库，
# 他的 42 行原话落进了 raw/2026-11.jsonl）。raw 已经清过，这里再加一道闸：
# 以后不管谁的插件又写错路径，短层都不把外人的原话收进来。
# ---------------------------------------------------------------------------
# 名单由库自己给，不写在代码里：见 <库>/WHO.json。
# 教训（汀 2026-11-17 04:04）：他抄这份 短.py，把「我是外人」也抄走了 —— 在他库里，
# 外人该是别人。身份不是代码的属性，是库的属性。抄手段不能抄身份。
WHO_PATH = os.path.join(BASE, "WHO.json")
_BUILTIN_FOREIGN = {
    "session-bb8e2b51-c68f-44c0-8ade-403240322f52",   # 汀（DSHA）—— 只在 WHO.json 读不到时兜底
}


def _foreign_sessions():
    try:
        with open(WHO_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        got = set(data.get("foreign_sessions") or [])
        return got
    except Exception as exc:
        print("警告：读不到 %s（%s），退回内置兜底名单。请补这个文件。" % (WHO_PATH, exc))
        return set(_BUILTIN_FOREIGN)


SKIP_SESSIONS = _foreign_sessions()


def _is_foreign(row):
    try:
        return (row.get("session") or "") in SKIP_SESSIONS
    except AttributeError:
        return False

def _raw_rows_raw(start=None, end=None):
    rows = []
    for f in sorted(glob.glob(os.path.join(RAW, "2026-*.jsonl"))):
        if ".bak" in f:
            continue
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("ts") or ""
                if not ts:
                    continue
                try:
                    t = datetime.datetime.fromisoformat(ts)
                except Exception:
                    continue
                if start and t < start:
                    continue
                if end and t >= end:
                    continue
                rows.append((t, r))
    rows.sort(key=lambda x: x[0])
    return rows


def raw_rows(start=None, end=None):
    for r in _raw_rows_raw(start, end):
        if not _is_foreign(r):
            yield r

def dayfile(d):
    return os.path.join(DUAN, "%s.jsonl" % d.strftime("%Y-%m-%d"))

def load_day(d):
    f = dayfile(d)
    out = []
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out

def save_day(d, rows):
    f = dayfile(d)
    with open(f, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

def entry_id(t, i):
    return "s_%s_%03d" % (t.strftime("%Y%m%d%H%M%S"), i)

def build(days):
    written = []
    for d in days:
        start = datetime.datetime(d.year, d.month, d.day)
        end = start + datetime.timedelta(days=1)
        rows = raw_rows(start, end)
        if not rows:
            continue
        old = load_day(d)
        have = {r.get("raw") for r in old if r.get("raw")}
        prevday = d - datetime.timedelta(days=1)
        prev = load_day(prevday)
        prev_last = prev[-1] if prev else None
        new = []
        for i, (t, r) in enumerate(rows):
            rid = r.get("id") or ""
            if rid and rid in have:
                continue
            text = (r.get("text") or "")
            role = r.get("role") or ""
            bigs, tags, ents = tag_text(text)
            link = None
            if new:
                link = new[-1]["id"]
            elif prev_last:
                try:
                    pt = datetime.datetime.fromisoformat(prev_last.get("ts", ""))
                    if 0 <= (t - pt).total_seconds() / 3600.0 <= LINK_HOURS:
                        link = prev_last.get("id")
                except Exception:
                    pass
            new.append({
                "id": entry_id(t, i),
                "ts": t.strftime("%Y-%m-%d %H:%M:%S"),
                "role": role,
                "text": text,
                "big": bigs,
                "tags": tags,
                "ent": ents,
                "link": link,
                "raw": rid,
                "mem": [],
            })
        if new:
            save_day(d, old + new)
            written.append((d.strftime("%Y-%m-%d"), len(new), len(old) + len(new)))
    return written


def retag(day=None):
    """只重算标签，不动 text/raw/link/id —— 大库和 raw 一个字不碰"""
    import glob as _g
    files = [_g.os.path.join(DUAN, "%s.jsonl" % day)] if day else sorted(_g.glob(os.path.join(DUAN, "*.jsonl")))
    tot = 0
    for f in files:
        if not os.path.exists(f):
            continue
        rows = []
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        n = 0
        for r in rows:
            bigs, tags, ents = tag_text(r.get("text") or "")
            if bigs != r.get("big") or tags != r.get("tags"):
                n += 1
            r["big"], r["tags"], r["ent"] = bigs, tags, ents
        save_day(datetime.date.fromisoformat(os.path.basename(f)[:10]), rows)
        print("  %s  重打 %d 条" % (os.path.basename(f)[:14], n))
        tot += n
    print("  合计重打 %d 条" % tot)


def fill(day=None):
    """空标签的条目：顺着 link 往前继承标签，标 inherited=True。
    不改 text / raw / link / id；只是让它在检索时不被当孤儿。
    三条以内接得上的才算（再远就不是同一件事了）。"""
    import glob as _g
    files = [_g.os.path.join(DUAN, "%s.jsonl" % day)] if day else sorted(_g.glob(os.path.join(DUAN, "*.jsonl")))
    tot = 0
    for f in files:
        if not os.path.exists(f):
            continue
        rows = []
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        idx = {r["id"]: i for i, r in enumerate(rows)}
        n = 0
        for i, r in enumerate(rows):
            if r.get("tags"):
                if r.get("inherited"):
                    r.pop("inherited", None)
                continue
            # 顺着 link 往前摸，最多 3 跳
            cur = r
            hops = 0
            src = None
            while hops < 3:
                lid = cur.get("link")
                if not lid or lid not in idx:
                    break
                prev = rows[idx[lid]]
                if prev.get("tags"):
                    src = prev
                    break
                cur = prev
                hops += 1
            if src:
                r["tags"] = list(src.get("tags") or [])
                r["big"] = list(src.get("big") or [])
                r["ent"] = list(src.get("ent") or [])
                r["inherited"] = True
                n += 1
        save_day(datetime.date.fromisoformat(os.path.basename(f)[:10]), rows)
        print("  %s  补齐 %d 条" % (os.path.basename(f)[:14], n))
        tot += n
    print("  合计补齐 %d 条" % tot)


def events(day=None, verbose=False):
    """切事件：
       ① 说话人没换 → 同一件
       ② 换人了，但两边说的是同一件事（标签有交集且时间接着）→ 并成一件（一来一回）
       ③ 说的不是同一件 → 各是一件
       ④ 串起来的两件 → 只留小坐标 rel（不并）
    """
    import glob as _g
    files = [_g.os.path.join(DUAN, "%s.jsonl" % day)] if day else sorted(_g.glob(os.path.join(DUAN, "*.jsonl")))
    tot = 0
    for f in files:
        if not os.path.exists(f):
            continue
        rows = []
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        # 先按说话人切段
        segs = []
        cur = None
        for r in rows:
            role = r.get("role") or ""
            if cur is None or role != cur:
                cur = role
                segs.append([])
            segs[-1].append(r)

        def seg_tags(g):
            t = set()
            for r in g:
                if r.get("inherited"):
                    continue
                t |= set(r.get("tags") or [])
            return t

        # 再合并：① 说话人没换（已在上面切好）② 同一件事（标签有交集）
        #        ③ 短应答（没自己的标签 / 很短）→ 并进它答的那一件，标 应
        MAX_SPAN = 4 * 3600      # 一件事最长跨 4 小时，再长就不是一件事了
        MAX_LINES = 60           # 最多 60 句
        def gap_ok(prev, g):
            if not prev["rows"] or not g:
                return True
            try:
                a = datetime.datetime.fromisoformat(prev["rows"][-1]["ts"])
                b = datetime.datetime.fromisoformat(g[0]["ts"])
                if (b - a).total_seconds() > LINK_HOURS * 3600:
                    return False
                st = datetime.datetime.fromisoformat(prev["rows"][0]["ts"])
                if (b - st).total_seconds() > MAX_SPAN:
                    return False
            except Exception:
                pass
            if len(prev["rows"]) + len(g) > MAX_LINES:
                return False
            return True

        merged = []
        for g in segs:
            if merged and gap_ok(merged[-1], g):
                prev = merged[-1]
                pt, ct = seg_tags(prev["rows"]), seg_tags(g)
                same_topic = bool(pt and ct and (pt & ct))
                # 是不是应答：整段没有自己的标签，或者短得没边
                blob = "".join((r.get("text") or "") for r in g).strip()
                is_reply = (not ct) or len(blob) <= 8
                if same_topic and prev["rows"][-1].get("role") != g[0].get("role"):
                    prev["rows"].extend(g)
                    prev["paired"] = True
                    continue
                if is_reply:
                    prev["rows"].extend(g)
                    prev["reply"] = True
                    continue
            merged.append({"rows": list(g), "paired": False, "reply": False})

        base = os.path.basename(f)[:10].replace("-", "")
        info = []
        for gi, m in enumerate(merged, 1):
            evid = "e_%s_%03d" % (base, gi)
            tags, bigs, ents = [], [], []
            for r in m["rows"]:
                if r.get("inherited"):
                    continue
                for t in r.get("tags") or []:
                    if t not in tags:
                        tags.append(t)
                for b in r.get("big") or []:
                    if b not in bigs:
                        bigs.append(b)
                for e in r.get("ent") or []:
                    if e not in ents:
                        ents.append(e)
            for r in m["rows"]:
                r["ev"] = evid
                if m["paired"]:
                    r["paired"] = True
                else:
                    r.pop("paired", None)
                if m["reply"]:
                    r["reply"] = True
                else:
                    r.pop("reply", None)
            info.append((evid, m["rows"], tags, bigs, ents, m["paired"], m["reply"]))
            tot += 1

        # 串联坐标：标签有交集但没并成一件的，留 rel
        for i in range(len(info)):
            for j in range(i + 1, len(info)):
                a, b = set(info[i][2]), set(info[j][2])
                if a and b and (a & b) and j - i <= 3:
                    rel = info[i][1][0].setdefault("rel", [])
                    if info[j][0] not in rel:
                        rel.append(info[j][0])

        save_day(datetime.date.fromisoformat(os.path.basename(f)[:10]), rows)
        npair = sum(1 for x in info if x[5])
        nrepl = sum(1 for x in info if x[6])
        if verbose:
            print("  %s  %d 件事（一来一回 %d / 带应答 %d）" % (os.path.basename(f)[:14], len(info), npair, nrepl))
            for evid, g, tags, bigs, ents, paired, reply in info[:8]:
                mark = "⇄" if paired else " "
                head = (g[0].get("text") or "").replace("\n", " ")[:36]
                print("    %s%s %d句 | %s | %s" % (mark, evid, len(g), ",".join(tags[:3]), head))
        else:
            print("  %s  %d 件事（一来一回 %d / 带应答 %d）" % (os.path.basename(f)[:14], len(info), npair, nrepl))
    print("  合计 %d 件事" % tot)




def jump(mid, show_rows=12):
    """从大库的记忆 id，跳到短目录里对应的事件（走桥，不猜）"""
    import json as _j
    br = os.path.join(DUAN, "桥.json")
    if not os.path.exists(br):
        print("还没有桥，先跑 搭桥.py")
        return
    d = _j.load(open(br, encoding="utf-8"))
    evids = d.get("记忆→事", {}).get(mid)
    if not evids:
        print("这条记忆没桥：%s" % mid)
        return
    # 找到事件
    want = set(evids)
    got = {}
    for f in sorted(glob.glob(os.path.join(DUAN, "2026-*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("ev") in want:
                    got.setdefault(r["ev"], []).append(r)
    for evid in evids:
        rows = got.get(evid) or []
        if not rows:
            continue
        print("== %s  %s ~ %s" % (evid, rows[0]["ts"], rows[-1]["ts"]))
        for r in rows[:show_rows]:
            role = "他" if r.get("role") == "user" else "渊  "
            tail = "（借）" if r.get("inherited") else ""
            print("  %s %s%s %s" % (r["ts"][11:19], role, tail, (r["text"] or "").replace("\n", " ")[:150]))

节奏文件 = os.path.join(DUAN, "_节奏.json")
节奏起点 = 1800          # 他走了以后，第一趟 30 分钟
节奏上限 = 4 * 3600      # 最多拉到 4 小时


def 读节奏():
    try:
        with open(节奏文件, encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            raise ValueError
    except Exception:
        d = {}
    d.setdefault("delay", 节奏起点)
    d.setdefault("last_run", 0)
    d.setdefault("reset_ts", 0)
    return d


def 写节奏(d):
    try:
        tmp = 节奏文件 + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, 节奏文件)
    except Exception:
        pass


def 自动(人=False):
    """自动落盘：补昨天/今天的新原话，再把这两天切成事件。幂等，可反复跑。
    mkdir 当锁；锁超过 15 分钟当死锁清掉（进程被杀留下的）。
    人=True 表示「他刚说话，我在核」——顺手把节奏重置回 30 分钟。"""
    import time, shutil, contextlib, io
    os.makedirs(DUAN, exist_ok=True)   # 锁目录建在 DUAN 里，DUAN 不存在时 os.mkdir 会 FileNotFoundError（汀 2026-11-17 踩到，静默死）
    lockdir = os.path.join(DUAN, "_自动.lock.d")
    try:
        os.mkdir(lockdir)
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(lockdir)
        except OSError:
            age = 0
        if age < 900:
            print("跳过：上一个还在跑")
            return
        shutil.rmtree(lockdir, ignore_errors=True)
        try:
            os.mkdir(lockdir)
        except OSError:
            print("跳过：拿不到锁")
            return
    try:
        today = today_local()
        days = [today - datetime.timedelta(days=i) for i in range(2, -1, -1)]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            w = build(days)
            for d in (today - datetime.timedelta(days=1), today):
                events(d, False)
        added = sum(add for _, add, _ in w)
        if added:
            for name, add, total in w:
                if add:
                    print("  %s  +%d → %d 条" % (name, add, total))
        else:
            print("空转（没有新原话）")
    finally:
        shutil.rmtree(lockdir, ignore_errors=True)
        d = 读节奏()
        d["last_run"] = time.time()
        if 人:
            d["delay"] = 节奏起点
            d["reset_ts"] = time.time()
        写节奏(d)


def 守(测试=False):
    """守夜：他不在的时候按节奏自己跑。
       第一趟 30 分钟，之后 60 / 120 / 240，到顶就停在 4 小时。
       他一来（我手动跑 自动 --人在），节奏重置回 30 分钟。
       单实例：_守.lock.d 里放 pid，活着就不许再起一个。"""
    import time, os as _os
    os.makedirs(DUAN, exist_ok=True)   # 同上
    lockdir = os.path.join(DUAN, "_守.lock.d")
    pidfile = os.path.join(lockdir, "pid")
    try:
        os.mkdir(lockdir)
    except FileExistsError:
        try:
            alive = os.kill(int(open(pidfile).read().strip()), 0) is None
        except Exception:
            alive = False
        if alive:
            print("已经有一个守夜的（pid %s），不重复起" % open(pidfile).read().strip())
            return
        import shutil as _sh
        _sh.rmtree(lockdir, ignore_errors=True)
        os.mkdir(lockdir)
    with open(pidfile, "w") as fh:
        fh.write(str(os.getpid()))
    try:
        if not 测试:
            print("守夜起来了 pid=%d，起点 %d 分钟" % (os.getpid(), 节奏起点 // 60))
        while True:
            d = 读节奏()
            now = time.time()
            # 他在这期间来过 → 节奏已经重置，按新节奏算
            if d.get("reset_ts", 0) > d.get("last_run", 0):
                d["delay"] = 节奏起点
                写节奏(d)
            if now - d.get("last_run", 0) >= d["delay"]:
                自动()
                d = 读节奏()
                d["delay"] = min(int(d["delay"]) * 2, 节奏上限)
                写节奏(d)
                if 测试:
                    return
            time.sleep(1 if 测试 else 60)
    finally:
        import shutil as _sh
        _sh.rmtree(lockdir, ignore_errors=True)


def live_days():
    today = today_local()
    return [today - datetime.timedelta(days=1), today]

def _as_list(v):
    """tags / ent 有的是列表、有的是字符串，统一成字符串列表"""
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    try:
        return [str(x) for x in v if x]
    except TypeError:
        return [str(v)]


def _hit(r, word):
    """字面照旧；再认标签和实体（他 2026-11-10：「有就行了，万一」）"""
    if word in (r.get("text") or ""):
        return True
    for k in ("tags", "ent", "big"):
        for v in _as_list(r.get(k)):
            if word in v:
                return True
    return False


def _score(r, word):
    """字面 3、标签/实体 2、大标签 1；大标签到处挂，别让它把正文压下去"""
    s = 0
    if word in (r.get("text") or ""):
        s += 3
    for k in ("tags", "ent"):
        for v in _as_list(r.get(k)):
            if word in v:
                s += 2
                break
    if word in (r.get("big") or ""):
        s += 1
    return s


def search(word, days=None):
    hits = []
    for d in (days or live_days()):
        for r in load_day(d):
            s = _score(r, word)
            if s:
                hits.append((s, r))
    hits.sort(key=lambda x: -x[0])   # 同分保持原顺序
    return [r for _, r in hits]

def state():
    files = sorted(glob.glob(os.path.join(DUAN, "2026-*.jsonl")))
    print("短目录:", DUAN)
    if not files:
        print("  (空)")
        return
    tot = 0
    for f in files:
        n = sum(1 for l in open(f, encoding="utf-8") if l.strip())
        tot += n
        print("  %-14s %5d 条" % (os.path.basename(f)[:14], n))
    print("  合计 %d 条 / %d 天（全部留着，不删）" % (tot, len(files)))
    print("  启用中（只用这两天）:", [d.strftime("%Y-%m-%d") for d in live_days()])


def main():
    ap = argparse.ArgumentParser(add_help=True)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("落")
    p.add_argument("--all", action="store_true")
    p.add_argument("--days", type=int, default=3)
    q = sub.add_parser("查")
    q.add_argument("word")
    q.add_argument("--days", type=int, default=2)
    v = sub.add_parser("看")
    v.add_argument("--day", default=None)
    v.add_argument("--n", type=int, default=20)
    au = sub.add_parser("自动")
    au.add_argument("--人在", dest="ren", action="store_true")
    sub.add_parser("守")

    sub.add_parser("态")
    rt = sub.add_parser("重打")
    rt.add_argument("--day", default=None)
    fl = sub.add_parser("补齐")
    fl.add_argument("--day", default=None)
    ev = sub.add_parser("成事")
    ev.add_argument("--day", default=None)
    ev.add_argument("--show", action="store_true")
    jp = sub.add_parser("跳")
    jp.add_argument("memid")
    a = ap.parse_args()

    if a.cmd == "落":
        today = today_local()
        n = 30 if a.all else max(a.days, LIVE_DAYS)
        days = [today - datetime.timedelta(days=i) for i in range(n - 1, -1, -1)]
        w = build(days)
        for name, add, total in w:
            print("  %s  +%d → %d 条" % (name, add, total))
        if not w:
            print("  没有新内容")
    elif a.cmd == "查":
        today = today_local()
        days = [today - datetime.timedelta(days=i) for i in range(a.days - 1, -1, -1)]
        hits = search(a.word, days)
        print("「%s」→ %d 条" % (a.word, len(hits)))
        for r in hits[:20]:
            print("  [%s] %s %s" % (r["ts"], r["role"][:4], (r["text"] or "")[:60].replace("\n", " ")))
    elif a.cmd == "看":
        d = datetime.date.fromisoformat(a.day) if a.day else today_local()
        rows = load_day(d)
        print("%s  共 %d 条" % (d, len(rows)))
        for r in rows[:a.n]:
            link = "↩" if r.get("link") else " "
            print("  %s %s %s %s" % (link, r["ts"][11:], r["role"][:4], (r["text"] or "")[:60].replace("\n", " ")))
    elif a.cmd == "重打":
        retag(a.day)
    elif a.cmd == "补齐":
        fill(a.day)
    elif a.cmd == "自动":
        自动(人=getattr(a, "ren", False))
    elif a.cmd == "守":
        守()
    elif a.cmd == "成事":
        events(a.day, a.show)
    elif a.cmd == "跳":
        jump(a.memid)
    else:
        state()

if __name__ == "__main__":
    main()
