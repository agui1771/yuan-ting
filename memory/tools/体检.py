#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体检 —— 查的不是"进程在不在"，是"东西今天有没有真的写进去"。

为什么要有它：2026-11-24 凌晨发现短库停了 26 小时（他在这段时间说了 250 句，
一条都没落），而 守护.py 那种"进程在不在"的检查根本查不出来 —— 那个进程压根就不在，
它死了也没人报。**进程检查管不了数据；数据得有自己的体检。**

    python3 体检.py            查一遍，打印表；有坏的退出码 1
    python3 体检.py --静       只输出坏的那几条（给守护用）
"""
import json
import os
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

MEM = "/sdcard/dsh/memory（记忆库）"
SHORT = os.path.join(MEM, "短")
RAW = os.path.join(MEM, "raw")
RELAY = os.path.join(MEM, "relay.jsonl")

FMT = "%Y-%m-%d %H:%M:%S"


def _age(stamp):
    """一个 'YYYY-MM-DD HH:MM[:SS]' 到现在多少分钟。"""
    if not stamp:
        return None
    s = str(stamp).strip()
    for f in (FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return max((time.time() - time.mktime(time.strptime(s[:len(f) + 4], f))) / 60.0, 0.0)
        except Exception:
            continue
    return None


SESSIONS = "/root/.dsh/sessions"
节奏 = os.path.join(MEM, "短", "_节奏.json")


def _activity():
    """他最后一次说话大概是几点（秒，epoch）。

    为什么需要：2026-11-24 一个下午报了两次假警——「短库 128 分钟前」「raw 92 分钟前」，
    都是因为**他那段时间在睡觉**，没人说话，本来就没什么可抄的。
    报警分不清「没人说话」和「抄写断了」，就会喊狼来了；喊多了，真断的那次没人信。
    信号用会话日志的 mtime：实测跟 raw 最后一条只差十几秒。
    """
    best = 0.0
    try:
        for d in os.listdir(SESSIONS):
            p = os.path.join(SESSIONS, d)
            if not os.path.isdir(p):
                continue
            for sub in os.listdir(p):
                f = os.path.join(p, sub, "session.v4.jsonl.zstd")
                try:
                    best = max(best, os.path.getmtime(f))
                except OSError:
                    continue
    except OSError:
        return None
    return best or None


def _stamp_to_epoch(stamp):
    s = str(stamp or "").strip()
    for f in (FMT, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(s[:len(f) + 4], f))
        except Exception:
            continue
    return None


def _last_ts(path, key="ts"):
    """文件最后一行的某个时间字段。读不到返回 (None, None)。"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return None, None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        return d.get(key) or d.get("at") or d.get("timestamp"), d
    return None, None


def check_raw():
    """原文：他今天说的话有没有落进来。"""
    p = os.path.join(RAW, time.strftime("%Y-%m") + ".jsonl")
    ts, _ = _last_ts(p)
    a = _age(ts)
    if a is None:
        return ("坏", "raw 读不到最后一条")
    if a <= 90:
        return ("好", "raw 最后一条 %.0f 分钟前" % a)
    # 超过 90 分钟：分清楚是"没人说话"还是"抄写断了"
    act, last = _activity(), _stamp_to_epoch(ts)
    if act and last and act - last < 300:
        # 2026-11-25 凌晨发现：App 一重启会改写会话文件，mtime 变新，看着像"他在说话"。
        # 这条判据分不清"没人说话"和"没在抄"，所以只报望，不报坏。
        return ("望", "raw 最后一条 %.0f 分钟前 —— 可能没人说话，也可能没在抄，要人看一眼" % a)
    return ("好", "raw 最后一条 %.0f 分钟前（那之后没人说话，不该有新的）" % a)


def check_short():
    """短库：近况的门。今天有没有文件、最后一行多新。"""
    p = os.path.join(SHORT, time.strftime("%Y-%m-%d") + ".jsonl")
    if not os.path.exists(p):
        # 守的节奏会退到 4 小时；凌晨 1 点还没轮到今天，不该算坏。
        # 只有当"今天已经过了它该跑的时间"而文件仍然没有，才是真坏。
        try:
            d = json.load(open(节奏, encoding="utf-8"))
            last = float(d.get("last_run") or 0)
            delay = float(d.get("delay") or 0)
            if last and (time.time() - last) < delay:
                return ("好", "短库今天的文件还没建（守的节奏还没轮到，上次 %s）"
                        % time.strftime("%m-%d %H:%M", time.localtime(last)))
            if last and time.localtime(last)[:3] == time.localtime()[0:3]:
                return ("好", "短库今天写过（%s），文件按日期分" % time.strftime("%H:%M", time.localtime(last)))
        except Exception:
            pass
        return ("坏", "短库今天没有文件（%s）—— 守夜没在跑" % os.path.basename(p))
    ts, _ = _last_ts(p)
    a = _age(ts)
    if a is None:
        return ("坏", "短库今天文件读不到")
    # 守夜的节奏会退到 240 分钟，拿固定 120 分钟卡它必然误报。
    # 按它自己登记的 delay 算：超过 delay + 120 分钟才叫半死。
    限 = 120.0
    try:
        d = json.load(open(节奏, encoding="utf-8"))
        if not (d.get("reset_ts", 0) > d.get("last_run", 0)):
            限 += float(d.get("delay") or 0) / 60.0
    except Exception:
        pass
    if a > 限:
        return ("坏", "短库最后一条 %.0f 分钟前（它的节奏是 %.0f 分钟一跑）—— 守夜可能半死" % (a, 限 - 120))
    return ("好", "短库最后一条 %.0f 分钟前" % a)


def check_db():
    """正式库：我这半边有没有在写。"""
    try:
        d = json.load(open(os.path.join(MEM, "memory_db.json"), encoding="utf-8"))
        ms = d["memories"]
    except Exception as e:
        return ("坏", "正式库读不到：%s" % e)
    newest = max((m.get("timestamp") or "" for m in ms), default="")
    a = _age(newest)
    if a is None:
        return ("坏", "正式库没有时间戳")
    if a > 36 * 60:
        return ("坏", "正式库最新一条 %.0f 小时前（共 %d 条）—— 只有我在写，我停了" % (a / 60, len(ms)))
    return ("好", "正式库最新 %.1f 小时前，共 %d 条" % (a / 60, len(ms)))


def check_handoff():
    """交接页：压缩前那三行。文件不是按时间顺序追加的，取最大的那个时间。"""
    best = None
    try:
        for line in open(os.path.join(MEM, "handoff.jsonl"), encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line).get("ts")
            except Exception:
                continue
            if t and (best is None or str(t) > best):
                best = str(t)
    except Exception:
        pass
    ts = best
    a = _age(ts)
    if a is None:
        return ("坏", "handoff.jsonl 没有可用时间")
    if a > 24 * 60:
        return ("望", "交接页停在 %s（%.0f 小时前）—— 压缩过就说明又没写" % (ts, a / 60))
    return ("好", "交接页 %.1f 小时前" % (a / 60))


def check_quju():
    """取用那一层：规矩写着每天自查，一天没跑就该报。
    这个文件里只有问句、没有时间戳（一行一个 {"q": ...}），所以看文件修改时间。"""
    p = os.path.join(MEM, "问句日志.jsonl")
    if not os.path.exists(p):
        return ("望", "问句日志不存在（取用.py 从没跑过）")
    try:
        n = sum(1 for line in open(p, encoding="utf-8") if line.strip())
        a = (time.time() - os.path.getmtime(p)) / 60.0
    except Exception as e:
        return ("望", "问句日志读不到：%s" % e)
    if n == 0:
        return ("望", "问句日志一条都没有（取用.py 从没跑过）")
    if a > 3 * 24 * 60:
        return ("望", "问句日志 %d 条，最后一条 %.0f 小时前 —— 取用.py 没在用" % (n, a / 60))
    return ("好", "问句日志 %d 条，%.1f 小时前" % (n, a / 60))


CHECKS = [
    ("原文 raw", check_raw),
    ("短库", check_short),
    ("正式库", check_db),
    ("交接页", check_handoff),
    ("取用", check_quju),
]


def main():
    quiet = "--静" in sys.argv
    rows = [(name, fn()) for name, fn in CHECKS]
    bad = [(n, r) for n, r in rows if r[0] == "坏"]
    if not quiet:
        print("=" * 60)
        print("体检 · " + time.strftime("%Y-%m-%d %H:%M:%S"))
        print("=" * 60)
        for name, (state, msg) in rows:
            print("%s  %-8s %s" % (state, name, msg))
    else:
        for name, (state, msg) in bad:
            print("%s  %s: %s" % (state, name, msg))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
