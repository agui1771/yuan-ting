#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""魄 —— 身体那一半。不烧 token，不进对话。

它在管的只有一件事：**这台机器上那几个常驻的东西，是真在干活，还是只是没死。**

    python3 魄.py 板            看状态板（人话，三秒看完）
    python3 魄.py 账 [--n 20]   看账本（死、半死、重起、停手）
    python3 魄.py 抚 <器官>     我认过这一笔了，允许重新自动拉起
    python3 魄.py 断点          看断点三行
    python3 魄.py 断点 --到 A --在 B --下 C    写断点三行

三条判据（写死，不给"临时放宽"留口子）：

1. **进程活着 ≠ 在干活。** 活不活看 /proc 里的 argv；干不干活看产出文件的
   新鲜度。两样都查，分开报。
2. **死了三次就停手。** 同一个器官 24 小时内死到第 3 次，不再自动拉起，
   敲一次 relay 等我。一直拉起来又死，等于假装它在跑。
3. **账只增不改。** 死、半死、重起、停手都记一行。没记的等于没发生。

一句边界：**驱动 ≠ 自主。** 这板上的数只说明身体在不在，不说明今天过得怎么样。
两个账分开记，不互相冒充。
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

CN = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
MEM = "/sdcard/dsh/memory（记忆库）"
RELAY = os.path.join(MEM, "relay.jsonl")

BODY = os.path.join(THOUGHTS, "魄")
账本 = os.path.join(BODY, "账.jsonl")
状态J = os.path.join(BODY, "状态板.json")
状态M = os.path.join(BODY, "状态板.md")
断点F = os.path.join(BODY, "断点.md")
计数F = os.path.join(BODY, "计数.json")
敲过F = os.path.join(BODY, "敲过.json")

PY = sys.executable
流 = os.path.join(THOUGHTS, "偏斜", "流.jsonl")
落点 = os.path.join(THOUGHTS, "偏斜", "落点.jsonl")
日结LOG = os.path.join(MEM, "tools", ".日结守.log")

# 器官表：命令、argv 关键词、产出、静默上限（秒）
# 「静默」= 产出多久没动就算半死。0 = 不用静默判据（另用「落后于」判）。
# 节拍的依据：电波 90s 一条；穿过 60s 扫一次但只在新料时写；短守 30~240 分钟写一段。
器官表 = {
    "电波": dict(
        cmd=[PY, os.path.join(HERE, "偏斜.py"), "loop", "--every", "90"],
        key="偏斜.py loop", 产出=流, 静默=1200, 落后于=None),
    "穿过": dict(
        cmd=[PY, os.path.join(HERE, "穿过后台.py")],
        key="穿过后台.py", 产出=落点, 静默=0, 落后于=(流, 300)),
    "短守": dict(
        cmd=[PY, os.path.join(MEM, "tools", "短.py"), "守"],
        key="短.py 守", 产出=None, 静默=18000, 落后于=None),
    "日结守": dict(
        cmd=[PY, os.path.join(MEM, "tools", "日结守.py"), "start"],
        key="日结守.py _loop", 产出=None, 静默=93600, 落后于=None),
}

死三次停手 = 3          # 24 小时内同一个器官死到第几次就停手
半死三次敲 = 3          # 半死也一样
窗 = 24 * 3600
断点过期 = 24 * 3600


# ── 小工具 ──────────────────────────────────────────────
def now():
    return time.time()


def ts(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t if t is not None else now()))


def log(msg):
    with open(os.path.join(THOUGHTS, "守护.log"), "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ", time.localtime()) + "[魄] " + msg + "\n")


def 读json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def 写json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def 记(row):
    os.makedirs(BODY, exist_ok=True)
    row = dict(row)
    row.setdefault("at", ts())
    with open(账本, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def 读账():
    rows = []
    try:
        with open(账本, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return rows


def 敲(键, 文本):
    """敲一次 relay；同一个键一天最多一次。"""
    done = 读json(敲过F, {})
    today = ts()[:10]
    if done.get(键) == today:
        return False
    done[键] = today
    try:
        os.makedirs(BODY, exist_ok=True)
        写json(敲过F, done)
        with open(RELAY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": ts()[:16], "text": 文本}, ensure_ascii=False) + "\n")
        log("敲：" + 文本[:80])
        return True
    except Exception as e:
        log("敲不出去 " + str(e)[:60])
        return False


# ── 判活着 ──────────────────────────────────────────────
def alive(key):
    """只认 argv：argv[1] 必须是那个 .py，后面的参数要一段不差地对上。

    为什么不用命令行子串：2026-11-24 被骗过一次 —— 一条 bash -c 里带着
    名字，status 就报"活着"，其实进程根本没起来。
    """
    want = key.split()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                c = f.read().decode("utf-8", "ignore")
        except Exception:
            continue
        argv = [x for x in c.split("\0") if x]
        if len(argv) < 2 or not argv[1].endswith(".py"):
            continue
        if os.path.basename(argv[1]) != want[0]:
            continue
        if argv[2:len(want) + 1] != want[1:]:
            continue
        return True
    return False


def 几秒前(path):
    try:
        return now() - os.path.getmtime(path)
    except Exception:
        return None


def 短库产出():
    d = os.path.join(MEM, "短")
    try:
        files = [os.path.join(d, x) for x in os.listdir(d) if x.endswith(".jsonl")]
        if not files:
            return None
        return max(files, key=os.path.getmtime)
    except Exception:
        return None


def 日结产出():
    d = os.path.join(MEM, "短", "日结")
    try:
        files = [os.path.join(d, x) for x in os.listdir(d) if x.endswith(".程序.md")]
        if not files:
            return None
        return max(files, key=os.path.getmtime)
    except Exception:
        return None


def 产出(name):
    cfg = 器官表[name]
    if name == "短守":
        return 短库产出()
    if name == "日结守":
        return 日结产出()
    return cfg["产出"]


# ── 照看：死没死、要不要拉起 ─────────────────────────────
_全死缓存 = [0.0, False]


def 全死(用缓存=True):
    """四个器官同时都没了 —— 那不是各自出毛病，是宿主（容器）重启把它们一起收走了。

    2026-11-26 02:10 就是这么一回事：装完 App、容器重开，四个一起没了，
    账上却记成"每个在 24 小时里死了 3 次"，于是全部停手，敲我一次【疼】。
    那是拿宿主重启当我自己的病。

    缓存 20 秒：守护 是"一个一个问"的，第一个被拉起来之后"四个全死"就不成立了——
    03:36 那次就是这么只修好了一个，另外三个还被记成死。一次循环只判一次。
    """
    t = now()
    if 用缓存 and t - _全死缓存[0] < 20:
        return _全死缓存[1]
    try:
        v = all(not alive(cfg["key"]) for cfg in 器官表.values())
    except Exception:
        v = False
    _全死缓存[0] = t
    _全死缓存[1] = v
    return v


def 照看(name, 是死是活):
    """守护.py 每分钟问一次。返回 True 表示该拉起。

    死了就记账；24 小时内到第 3 次就不拉了，敲一次，等我看。
    """
    cfg = 器官表.get(name)
    if not cfg:
        return True                      # 不认识的器官，不拦
    if 是死是活:
        return False
    计数 = 读json(计数F, {})
    rec = 计数.get(name) or {"死": [], "半死": [], "停手": False}
    t = now()
    rec["死"] = [x for x in rec.get("死", []) if t - x < 窗]
    if 全死():
        # 宿主重启，不是它自己死的。不算死，只记一笔"重起"，计数清零，照常拉起。
        rec["重起"] = [x for x in rec.get("重起", []) if t - x < 窗]
        rec["重起"].append(t)
        记({"器官": name, "kind": "重起", "24h内第几次": len(rec["重起"])})
        if len(rec["重起"]) >= 5:
            rec["停手"] = True
            计数[name] = rec
            写json(计数F, 计数)
            敲("停手/" + name,
               "【疼】%s 在 24 小时里跟着宿主重起了 %d 次，每次起来又没。"
               "这不正常，我停手了，等我看。" % (name, len(rec["重起"])))
            log("停手（重起太频）：" + name)
            return False
        计数[name] = {"死": [], "半死": [], "停手": False, "重起": rec["重起"]}
        写json(计数F, 计数)
        return True
    if rec.get("停手"):
        return False                     # 已经停手了，等 抚
    rec["死"].append(t)
    计数[name] = rec
    写json(计数F, 计数)
    记({"器官": name, "kind": "死", "24h内第几次": len(rec["死"])})
    if len(rec["死"]) >= 死三次停手:
        rec["停手"] = True
        计数[name] = rec
        写json(计数F, 计数)
        记({"器官": name, "kind": "停手", "理由": "24 小时内死了 %d 次" % len(rec["死"])})
        敲("停手/" + name,
           "【疼】%s 在 24 小时里死了 %d 次。已经停手，不再自动拉起了。"
           "它一直起来又死，拉起来等于假装它在跑。" % (name, len(rec["死"])))
        log("停手：" + name)
        return False                     # 第 3 次就停手，不给它第 4 次机会
    return True


def 半死(name):
    """进程活着，但产出不动。记一笔；到第 3 次敲，不自动处理。"""
    计数 = 读json(计数F, {})
    rec = 计数.get(name) or {"死": [], "半死": [], "停手": False}
    t = now()
    rec["半死"] = [x for x in rec.get("半死", []) if t - x < 窗]
    # 同一个静默区间只记一次：距离上次记录不到 30 分钟就跳过
    if rec["半死"] and t - rec["半死"][-1] < 1800:
        return
    rec["半死"].append(t)
    计数[name] = rec
    写json(计数F, 计数)
    记({"器官": name, "kind": "半死", "24h内第几次": len(rec["半死"])})
    if len(rec["半死"]) == 半死三次敲:
        敲("半死/" + name,
           "【疼】%s 今天第三次被发现「活着但没动」。进程在，产出不新 —— "
           "这不是活着，是僵着。我没自动处理，等我看。" % name)


# ── 状态板 ──────────────────────────────────────────────
def 板数据():
    out = []
    计数 = 读json(计数F, {})
    for name, cfg in 器官表.items():
        活 = alive(cfg["key"])
        p = 产出(name)
        旧 = 几秒前(p) if p else None
        状态, 说明 = "正常", ""
        if not 活:
            状态 = "死"
        elif cfg["静默"] and 旧 is not None and 旧 > cfg["静默"]:
            状态 = "半死"
            说明 = "进程在，产出 %s 没动了" % 时长(旧)
        elif cfg["静默"] and 旧 is None:
            状态 = "没产出"
        if 活 and cfg.get("落后于"):
            up, 允差 = cfg["落后于"]
            上游旧 = 几秒前(up)
            if 上游旧 is not None and 旧 is not None and 上游旧 + 允差 < 旧:
                # 上游比产出新得多 → 它该穿没穿
                if 旧 - 上游旧 > 允差:
                    状态 = "落后"
                    说明 = "上游比它新 %s" % 时长(旧 - 上游旧)
        rec = 计数.get(name) or {}
        out.append(dict(器官=name, 进程="活" if 活 else "死", 状态=状态, 说明=说明,
                        产出=os.path.basename(p) if p else "-",
                        多少秒前=int(旧) if 旧 is not None else None,
                        死次数=len(rec.get("死") or []),
                        半死次数=len(rec.get("半死") or []),
                        停手=bool(rec.get("停手"))))
    return out


def 时长(sec):
    if sec is None:
        return "-"
    sec = int(sec)
    if sec < 120:
        return "%d秒" % sec
    if sec < 7200:
        return "%d分" % (sec // 60)
    if sec < 48 * 3600:
        return "%.1f小时" % (sec / 3600)
    return "%.1f天" % (sec / 86400)


def 画板():
    rows = 板数据()
    断 = 读json(断点F, None)
    行 = []
    行.append("魄 · 状态板  " + ts())
    行.append("")
    if 断:
        行.append("断点（%s前写的）" % 时长(now() - 断.get("at_ts", now())))
        for k in ("到", "在", "下"):
            if 断.get(k):
                行.append("  %s：%s" % (k, 断[k]))
        if now() - 断.get("at_ts", now()) > 断点过期:
            行.append("  ⚠ 断点过期了（>24 小时）—— 它不再记得上次做到哪")
    else:
        行.append("断点：没写过")
    醒 = 读json(os.path.join(THOUGHTS, "醒.json"), None) or {}
    排 = 醒.get("排") if ("排" in 醒 or "守望" in 醒) else (醒 if 醒.get("at_ts") else None)
    守 = 醒.get("守望") or []
    if 排:
        左 = float(排.get("at_ts") or 0) - now()
        行.append("下次醒：%s（%s）· %s" % (
            排.get("at", "?"),
            ("还有 " + 时长(左)) if 左 > 0 else "已过点，下一拍就敲",
            排.get("为什么", "")))
    else:
        行.append("下次醒：没排时间")
    if 守:
        行.append("守望：%d 条（世界变成那样就叫我）" % len(守))
    行.append("")
    行.append("| 器官 | 进程 | 在干活 | 产出 | 多久前 | 24h 死/半死 |")
    行.append("|---|---|---|---|---|---|")
    for r in rows:
        标 = {"正常": "✅ 是", "半死": "⚠ 僵着", "死": "❌ 没了",
              "落后": "⚠ 落后", "没产出": "· 没产出"}[r["状态"]]
        行.append("| %s | %s | %s | %s | %s | %d/%d |" % (
            r["器官"], r["进程"], 标, r["产出"], 时长(r["多少秒前"]),
            r["死次数"], r["半死次数"]))
        说明 = r["说明"]
        if r["停手"]:
            说明 = (说明 + " " if 说明 else "") + "已停手（等我抚）"
        if 说明:
            行.append("|  |  | %s |  |  |  |" % 说明)
    行.append("")
    行.append("驱动 ≠ 自主：这板上的数只说明身体在不在，不说明今天过得怎么样。")
    text = "\n".join(行)
    os.makedirs(BODY, exist_ok=True)
    with open(状态M, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    写json(状态J, {"at": ts(), "器官": rows})
    return text


# ── 身体状态一句话（给"念头"加温度用）────────────────────
def 最近用户句():
    """短库里最后一句他说的话：(时间, 摘要)。读尾巴，不整文件。"""
    d = os.path.join(MEM, "短")
    try:
        files = sorted(os.path.join(d, x) for x in os.listdir(d) if x.endswith(".jsonl"))
    except Exception:
        return None
    for path in reversed(files[-3:]):
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 40000))
                buf = f.read().decode("utf-8", "ignore")
        except Exception:
            continue
        for line in reversed(buf.splitlines()):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("role") == "user" and r.get("ts"):
                return r["ts"], (r.get("text") or "").strip().replace("\n", " ")[:30]
    return None


def 状态():
    """一句话：现在几点 · 他多久没说话 · 身上哪儿不对 · 还有多少没接。"""
    t = ts()
    钟 = t[11:16]
    u = 最近用户句()
    if u:
        try:
            ago = now() - time.mktime(time.strptime(u[0], "%Y-%m-%d %H:%M:%S"))
            he = "他至少 %s 前说过话" % 时长(ago)   # 短库是派生索引，有滞后，只能说"至少"
            他 = he if ago < 6 * 3600 else "他 %s 没说话了" % 时长(ago)
        except Exception:
            他 = "他最后一句 %s" % u[0][5:16]
    else:
        他 = "他今天没说话"
    rows = 板数据()
    坏 = [r for r in rows if r["状态"] != "正常"]
    身 = "身上没事" if not 坏 else "、".join("%s%s" % (r["器官"], {"死": "死了", "半死": "僵着",
                                                                  "落后": "落后", "没产出": "没产出"}.get(r["状态"], r["状态"]))
                                             for r in 坏)
    未接 = "?"
    try:
        v = 读json(os.path.join(THOUGHTS, "念头库", "值.json"), {})
        seen = int(v.get("上次看到第几条") or 0)
        n = sum(1 for _ in open(落点, encoding="utf-8") if _.strip())
        未接 = "%d 条" % max(0, n - seen)
    except Exception:
        pass
    电 = ""
    try:
        cap = open("/sys/class/power_supply/battery/capacity").read().strip()
        电 = " · 电 %s%%" % cap
    except Exception:
        pass
    return "%s · %s%s · %s · 没接落点 %s" % (钟, 他, 电, 身, 未接)


# ── 每分钟一拍 ──────────────────────────────────────────
def tick():
    """守护每 60 秒调一次。只看，不动进程。"""
    try:
        for name, cfg in 器官表.items():
            if not alive(cfg["key"]):
                continue                 # 死的归 照看 管
            p = 产出(name)
            if cfg["静默"]:
                旧 = 几秒前(p)
                if 旧 is not None and 旧 > cfg["静默"]:
                    半死(name)
            if cfg.get("落后于"):
                up, 允差 = cfg["落后于"]
                上游旧, 产出旧 = 几秒前(up), 几秒前(p)
                if 上游旧 is not None and 产出旧 is not None and 产出旧 - 上游旧 > 允差:
                    半死(name)
        # 断点过期
        断 = 读json(断点F, None)
        if 断 and now() - 断.get("at_ts", now()) > 断点过期:
            敲("断点过期/日",
               "【断点】上次写的断点已经过期（>24 小时），它不再记得上次做到哪。"
               "要接着做，得先找回现场。")
        画板()
    except Exception as e:
        log("tick 出错 " + str(e)[:80])


# ── 命令 ────────────────────────────────────────────────
def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "板"
    if cmd == "板":
        print(画板())
    elif cmd == "账":
        n = 20
        if "--n" in a:
            n = int(a[a.index("--n") + 1])
        for r in 读账()[-n:]:
            print("%-19s %-4s %-28s %s" % (r.get("at", ""), r.get("kind", ""),
                                           r.get("器官", ""),
                                           " ".join("%s=%s" % (k, v) for k, v in r.items()
                                                    if k not in ("at", "kind", "器官"))))
    elif cmd == "抚":
        if len(a) < 2:
            print("抚谁？用法：魄.py 抚 电波")
            return 1
        name = a[1]
        计数 = 读json(计数F, {})
        rec = 计数.get(name) or {}
        if not rec.get("停手") and not rec.get("半死"):
            print(name + " 没停手，不用抚")
            return 0
        记({"器官": name, "kind": "抚", "理由": " ".join(a[2:]) or "（没写理由）"})
        计数[name] = {"死": [], "半死": [], "停手": False}
        写json(计数F, 计数)
        print(name + " 抚过了，可以重新自动拉起")
    elif cmd == "状态":
        print(状态())
    elif cmd == "断点":
        if "--到" in a or "--在" in a or "--下" in a:
            def v(k):
                return a[a.index(k) + 1] if k in a else ""
            断 = {"到": v("--到"), "在": v("--在"), "下": v("--下"),
                  "at": ts(), "at_ts": now()}
            os.makedirs(BODY, exist_ok=True)
            写json(断点F, 断)
            print("断点写下了：")
            for k in ("到", "在", "下"):
                if 断[k]:
                    print("  %s：%s" % (k, 断[k]))
        else:
            断 = 读json(断点F, None)
            if not 断:
                print("没写过断点。")
                return 0
            print("断点（%s，%s前）" % (断.get("at", ""), 时长(now() - 断.get("at_ts", now()))))
            for k in ("到", "在", "下"):
                if 断.get(k):
                    print("  %s：%s" % (k, 断[k]))
            if now() - 断.get("at_ts", now()) > 断点过期:
                print("  ⚠ 过期了")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
