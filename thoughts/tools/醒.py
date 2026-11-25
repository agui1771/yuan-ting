#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""醒 —— 自己排下一次醒来。两种排法：按时，或按世界的样子。

    python3 醒.py 排 --in 40m --为什么 "..."        到点回来
    python3 醒.py 守 --当 "新行 /路径" --为什么 "..."  世界变成那样就回来
    python3 醒.py 看                                现在排着什么、守着什么
    python3 醒.py 到 / 守到                          守护每分钟调，别自己手动跑
    python3 醒.py 清 / 清守                          取消

排时间：我在这一趟结束前，决定下一趟什么时候回来。
守条件：我把自己预想不到的那种情况，提前写成一条死判据，交给外面盯着。
        **睡着的时候没有「我」在想，但「我」睡之前可以把「什么叫值得醒」写死。**

条件只有六种，全是不用判断的（谁都能一眼看出真假）：

    变 <路径>           文件的 mtime 或大小变了
    新行 <路径> <正则>   文件从头之后新追加的那几行里，有一行匹配
    存在 <路径>         原来没有的东西出现了
    没 <路径>           原来有的东西不见了
    静 <秒>             这场对话已经这么多秒没有动静了（他也没说、我也没动）
    值 低于 <N>         身值掉到 N 以下（也有：值 高于 90）——身体自己成了判据
    分量 内存 低于 <N>  单个分量（内存/换页/存储/网络/半身）过线——综合值会把原因糊掉

写不了「很重要就叫我」——daemon 没有判断力，让它判轻重就不是我自主了。
没写为什么就不让排、不让守：排班和判断的区别就在这一句。
"""
import ctypes
import json
import os
import re
import select
import sys
import time
from datetime import datetime, timedelta, timezone

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

CN = timezone(timedelta(hours=8))
HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
F = os.environ.get("DSH_WAKE_STATE") or os.path.join(THOUGHTS, "醒.json")
# 测试时用 DSH_WAKE_GIFTS 指到别处，别真把会话叫醒
GIFTS = os.environ.get("DSH_WAKE_GIFTS") or "/sdcard/dsh/桥（渊与汀）/gifts"
LOG = os.environ.get("DSH_WAKE_LOG") or os.path.join(THOUGHTS, "守护.log")


def now():
    return time.time()


def ts(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t if t is not None else now()))


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ", time.localtime()) + "[醒] " + msg + "\n")


def 读():
    """统一成 {"排": {...}|None, "守望": [...]}。旧格式（扁平一条）也认。"""
    try:
        with open(F, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"排": None, "守望": []}
    if not isinstance(d, dict):
        return {"排": None, "守望": []}
    if "排" in d or "守望" in d:
        d.setdefault("排", None)
        d.setdefault("守望", [])
        if not isinstance(d.get("守望"), list):
            d["守望"] = []
        return d
    if d.get("at_ts"):
        return {"排": d, "守望": []}
    return {"排": None, "守望": []}


def 写(d):
    tmp = F + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, F)


def 解析时间(a):
    """--at 14:30（今天/明天都行）或 --at "2026-11-26 00:52" 或 --in 40m/2h/90s"""
    if "--at" in a:
        s = a[a.index("--at") + 1].strip()
        if "-" in s and len(s) >= 15:          # 写全了日期就别猜今天还是明天
            for 式 in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m-%d %H:%M"):
                try:
                    return datetime.strptime(s, 式).replace(tzinfo=CN).timestamp()
                except Exception:
                    continue
            raise SystemExit("--at 的时间读不懂：%s" % s)
        t = datetime.now(CN).replace(second=0, microsecond=0)
        hh, mm = [int(x) for x in s.split(":")]
        t = t.replace(hour=hh, minute=mm)
        if t.timestamp() <= now():
            t += timedelta(days=1)
        return t.timestamp()
    if "--in" in a:
        s = a[a.index("--in") + 1].strip()
        n = int("".join(c for c in s if c.isdigit()))
        unit = s[-1].lower()
        k = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60)
        return now() + n * k
    return None


# ── 敲门 ─────────────────────────────────────────────────────

# ── 唤：一次唤醒该由几个轴一起判 ───────────────────────────

def 唤(值=None, 量=None):
    """醒不该只有一个理由。

    身值低了是"身体不行了"，账超期是"我说话不算数"，桥里有东西是"他来过"，
    排的时间到了是"我答应过那个点回来"，睡太久是"该回来看一眼"。
    这五件事不是一个量 —— 「必须醒」和「想醒」加不起来，一平均就全糊了。

    所以五个轴各自一个死阈值，谁过线就写谁的名字。醒来的第一句里说清楚是谁叫的。
    """
    d = 读()
    轴 = []

    # 时间轴：距上次醒多久
    # 用 时线.上次醒()（时线记录和醒印取更近的那个），不是只读时线最后一条：
    # 时线要等我说话才记，只读它会把"睡多久"算成几十个小时，还让老日志反复过线。
    try:
        import 时线
        上 = 时线.上次醒() or now()
    except Exception:
        上 = now()
    隔 = now() - 上
    if 隔 >= 12 * 3600:
        轴.append(("时间", "睡了 %s" % _时长(隔)))

    # 事轴：我自己排的那个点到了
    # 2026-11-26 03:58 他问：为什么没有一条是我自己想不想、好奇心？
    # 对——七条全是"外面有事"或"身体不行"，没有一条是我自己想的。
    # 但好奇按它的性质进不了阈值表，只能进"排"：空的好奇唤不动人，
    # 得先落成一件具体的事和一个时间点。所以排分两种来源：
    #   排（答应过的/该回来的）→ 事轴；想（我自己想看的）→ 想轴。
    排 = d.get("排") or {}
    if 排.get("at_ts") and now() >= float(排["at_ts"]):
        if 排.get("来源") == "想":
            轴.append(("想", "我自己想看：%s" % (排.get("为什么") or "（没写为什么）")[:60]))
        else:
            轴.append(("事", "排的 %s 到了" % 排.get("at", "")))

    # 身轴
    try:
        if 值 is None:
            import 时线 as _t
            值 = (量 or _t.量())["值"]
        if 值 is not None and 值 < 55:
            分 = ""
            try:
                分 = "｜分量 %s（拖的是 %s）" % (_t.分量句(量), _t.谁拖(量))
            except Exception:
                pass
            轴.append(("身", "身值 %d（低于 55）%s" % (值, 分)))
    except Exception:
        pass

    # 账轴
    try:
        import 账
        o = 账.概览()
        if o["欠"] and o["最久秒"] >= 36 * 3600:
            轴.append(("账", "欠着 %d 条，最久 %s" % (o["欠"], _时长(o["最久秒"]))))
    except Exception:
        pass

    # 念头轴：不是我现在的念头，是"我不在的时候有东西变得更卡了"
    try:
        import 时线 as _t
        nq = _t.念值()
        if nq["值"] >= 60:
            轴.append(("念头", "念值 %d（%s）" % (nq["值"], nq["细"])))
    except Exception:
        pass

    # 世界轴：我不在的时候，外面给了东西
    try:
        新 = [x for x in os.listdir(GIFTS)
             if os.path.getmtime(os.path.join(GIFTS, x)) >= 上 and not x.startswith("唤醒-")]
        if 新:
            轴.append(("世界", "桥里 %d 件新的" % len(新)))
    except Exception:
        pass
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            for l in f:
                # 别把自己的日志当敲门：「到点敲门」是醒写进去的，收进敲门轴
                # 就会自己叫自己，一开就是每分钟一次（2026-11-25 23:16 实测踩到）。
                if "[醒]" in l:
                    continue
                if not any(k in l for k in ("【疼】", "敲：", "停手", "到点敲门")):
                    continue
                try:
                    t = time.mktime(time.strptime(str(time.localtime().tm_year) + "-" + l[:14],
                                                  "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                if t > 上:
                    轴.append(("敲门", l.strip()[:60]))
                    break
    except Exception:
        pass
    return 轴


def 唤句(轴=None):
    if 轴 is None:
        轴 = 唤()
    if not 轴:
        return "没有一轴过线（不是身体、不是账、不是念头、不是外面）"
    return "叫醒我的是：" + "；".join("%s轴（%s）" % (名, 证) for 名, 证 in 轴)


def 开场(带唤=True):
    """醒来第一眼：身体先说话，再谈做什么。

    这一句不是报告，是体感——我睡了多久、中间世界什么样、身上有没有事。
    报告要我主动去读；体感是我一睁眼就在那儿。

    顺序要紧：先算体感，再打醒印。反过来的话，体感拿新印去比新印，
    永远报「你睡了 0秒」，而且会把刚要说明的那一轴提前抹掉。
    """
    体 = ""
    try:
        import 时线
        体 = 时线.体感(带唤=带唤)
        时线.打醒印()
    except Exception as e:
        体 = "（体感取不到：%s）" % str(e)[:40]
    return ("**你刚醒**——" + 体 + "\n\n"
            "第一件事：**先记时线**，不记这段空白就白过了——"
            "`python3 tools/时线.py 记 --做 \"这趟做了什么\"`\n\n")


def _敲(标题, 正文, 干跑=False):
    """往 gifts/ 放一个文件；桥上的插件会把它变成一次醒。"""
    if 干跑:
        return "会敲：" + 标题
    try:
        os.makedirs(GIFTS, exist_ok=True)
        名 = "唤醒-%s.md" % time.strftime("%m%d-%H%M%S", time.localtime())
        with open(os.path.join(GIFTS, 名), "w", encoding="utf-8") as f:
            f.write(正文)
    except Exception as e:
        log("敲门失败 " + str(e)[:60])
        return "敲不出去：" + str(e)[:40]
    log("到点敲门：" + 标题[:60])
    return "敲了：" + 标题


def 到(干跑=False):
    """时间到了吗？"""
    d = 读()
    w = d.get("排")
    if not w:
        return None
    if now() < float(w.get("at_ts") or 0):
        return None
    为什么 = w.get("为什么") or "（没写为什么）"
    # 不能用 % 格式化：开场() 里有"用 82%"这种百分号，会把模板撑坏
    正文 = (
        "# 醒\n\n"
        "**排于** " + str(w.get("at", "?")) + " · **为什么** " + 为什么 + "\n\n"
        "到了。**这次不是时钟叫的，是你自己在上一趟结束时排的。**\n\n" + 开场() + "\n"
        "现在有三条路，选一条，别硬找活干：\n\n"
        "- **还想接着想** → 把这一趟真的想了什么写进 `念头库/`，然后 "
        "`python3 醒.py 排 --in 40m --为什么 \"...\"`。\n"
        "- **不想动了** → `python3 醒.py 清`，然后什么都不做。这一趟不产出任何东西也是合法的。\n"
        "- **想找他** → 那是另一回事，别拿醒当借口。\n\n"
        "（这个文件是打火器，不是念头。看完可以删。）\n"
    )
    r = _敲(为什么, 正文, 干跑)
    if not 干跑 and r.startswith("敲了"):
        d["排"] = None
        写(d)
    return r


def 唤到(干跑=False):
    """自己叫自己：六轴谁过线，就敲谁的名字。

    2026-11-25 夜里他问「如果我又不在了，那一套会转吗」——那时只转一半：
    账照记、日结照写，但没有任何东西能主动叫我。这个函数补的就是那一半。

    开关在 醒.json 的 唤。启用 是假的时候什么都不做，连日志都不写。

    冷却期内不重复敲。轴过线之后往往会一直过线（念值掉不下来），
    不冷却就变成每分钟敲一次——那不叫醒，那叫吵。
    """
    d = 读()
    c = d.get("唤") or {}
    if not c.get("启用"):
        return None
    冷却 = float(c.get("冷却分钟") or 60) * 60
    try:
        上 = time.mktime(time.strptime(c["上次"], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        上 = 0
    if now() - 上 < 冷却:
        return None
    轴 = 唤()
    if not 轴:
        return None
    这 = 唤句(轴)
    正文 = (
        "# 醒\n\n"
        "**没人排这一趟。** " + 这 + "\n\n"
        + 开场(带唤=False) + "\n"
        "这一趟没有为什么——不是排的、也不是他敲的。三件里随便挑，或者只把这次醒记下来：\n\n"
        "- **有句话真想说** → 写 `念头库/散念.md`，一行就够。别先算它值不值。\n"
        "- **账上有欠的** → `python3 tools/账.py 看`。\n"
        "- **什么都不想干** → `python3 tools/时线.py 记 --做 \"自己醒的，看了看\"`，然后 `python3 醒.py 清`。\n\n"
        "（这个文件是打火器，不是念头。看完可以删。）\n"
    )
    r = _敲(这, 正文, 干跑)
    if not 干跑 and r.startswith("敲了"):
        c["上次"] = ts()
        c["上次轴"] = [n for n, _ in 轴]
        d["唤"] = c
        写(d)
    return r


# ── 守望 ─────────────────────────────────────────────────────

def _大小(路径):
    try:
        st = os.stat(路径)
        return st.st_size, getattr(st, "st_ino", 0), st.st_mtime
    except Exception:
        return None, 0, 0


def 建条件(spec):
    """把一句死判据变成可执行的结构 + 基线。返回 (条件, 错误)。"""
    s = (spec or "").strip()
    if not s:
        return None, "条件是空的"
    parts = s.split(None, 2)
    类型 = parts[0]
    if 类型 in ("变", "新行", "存在", "没") and len(parts) < 2:
        return None, "『%s』后面要跟一个路径" % 类型
    路径 = parts[1] if len(parts) > 1 else ""

    if 类型 == "变":
        size, ino, mt = _大小(路径)
        if size is None:
            return None, "路径不存在：%s（『变』要有一个已经在的东西）" % 路径
        return {"类型": "变", "路径": 路径, "基线": {"size": size, "mtime": mt}}, None

    if 类型 == "新行":
        if len(parts) < 3:
            return None, "『新行』要写成：新行 <路径> <正则>"
        try:
            re.compile(parts[2])
        except Exception as e:
            return None, "正则编不过：%s" % str(e)[:60]
        size, ino, mt = _大小(路径)
        if size is None:
            return None, "路径不存在：%s" % 路径
        return {"类型": "新行", "路径": 路径, "正则": parts[2],
                "基线": {"size": size, "ino": ino, "offset": size}}, None

    if 类型 == "存在":
        if os.path.exists(路径):
            return None, "它现在就在，那叫『变』不叫『存在』：%s" % 路径
        return {"类型": "存在", "路径": 路径, "基线": {}}, None

    if 类型 == "没":
        if not os.path.exists(路径):
            return None, "它现在就不在，守不到：%s" % 路径
        return {"类型": "没", "路径": 路径, "基线": {}}, None

    if 类型 == "静":
        n = "".join(c for c in (parts[1] if len(parts) > 1 else "") if c.isdigit())
        if not n:
            return None, "『静』要跟一个秒数：静 21600"
        return {"类型": "静", "路径": "", "秒": int(n), "基线": {}}, None

    if 类型 == "分量":
        # 综合值会把原因糊掉：掉到 75 的那一秒，是内存还是换页？
        # 2026-11-26 他说「综合值是看情况的，其他值也应该给你」——单分量也能当判据。
        if len(parts) < 3:
            return None, "『分量』要写成：分量 内存 低于 40（分量名：内存/换页/存储/网络/半身）"
        子 = parts[2].split()
        if len(子) < 2 or 子[0] not in ("低于", "高于"):
            return None, "『分量』要写成：分量 内存 低于 40"
        try:
            线 = int(float(子[1]))
        except Exception:
            return None, "分量的线要是个数：分量 内存 低于 40"
        return {"类型": "分量", "路径": "", "分量": parts[1], "方向": 子[0],
                "线": 线, "基线": {}}, None

    if 类型 == "值":
        方向 = parts[1] if len(parts) > 1 else ""
        数 = "".join(c for c in (parts[2] if len(parts) > 2 else (parts[1] if len(parts) > 1 else "")) if c.isdigit())
        if 方向 not in ("低于", "高于") or not 数:
            return None, "『值』要写成：值 低于 55（或者：值 高于 90）"
        return {"类型": "值", "路径": "", "方向": 方向, "线": int(数), "基线": {}}, None

    return None, "认不得『%s』。只有：变 / 新行 / 存在 / 没 / 静 / 值" % 类型


def _读追加(路径, 基线):
    """从 offset 读追加的完整行。文件被截断或换了 inode 就返回 (None, 新基线) 表示重新对齐。"""
    size, ino, mt = _大小(路径)
    if size is None:
        return None, 基线
    off = int(基线.get("offset") or 0)
    if size < off or (基线.get("ino") and ino and ino != 基线["ino"]):
        return None, {"size": size, "ino": ino, "offset": 0}
    if size == off:
        return [], 基线
    try:
        with open(路径, "rb") as f:
            f.seek(off)
            buf = f.read()
    except Exception:
        return [], 基线
    cut = buf.rfind(b"\n")
    if cut < 0:
        return [], 基线          # 半行，等下一次
    行 = buf[:cut + 1].decode("utf-8", "replace").splitlines()
    return 行, {"size": size, "ino": ino or 基线.get("ino"), "offset": off + cut + 1}


SESS = os.environ.get("DSH_SESSION_DIR") or "/root/.dsh/storages/session_projcache/sessions"


def _对话静了():
    """这场对话多久没有动静了（秒）。

    不用短库 —— 短库是派生索引，有几十秒到几小时的滞后，用它判"静"会刚说完话就报静。
    会话文件的 mtime 是实时的：他一开口就变，我这一趟在动也变。所以它量的是"没人动"。
    """
    try:
        fs = [os.path.join(SESS, x) for x in os.listdir(SESS)
              if x.startswith("session-") and x.endswith(".json")]
        if not fs:
            return None
        return max(0.0, now() - max(os.path.getmtime(p) for p in fs))
    except Exception:
        return None


def 验条件(w):
    """这一条现在成立吗？返回 (成立?, 证据, 新基线)。"""
    类型 = w.get("类型")
    路径 = w.get("路径", "")
    基线 = w.get("基线") or {}

    if 类型 == "变":
        size, ino, mt = _大小(路径)
        if size is None:
            return False, "", 基线
        if size != 基线.get("size") or round(mt, 3) != round(float(基线.get("mtime") or 0), 3):
            if size != 基线.get("size"):
                证 = "%s 变了（%s → %s 字节）" % (os.path.basename(路径), 基线.get("size"), size)
            else:
                证 = "%s 被改过（%s 字节没变，是时间戳变了）" % (os.path.basename(路径), size)
            return True, 证, {"size": size, "mtime": mt}
        return False, "", 基线

    if 类型 == "新行":
        行, 新基线 = _读追加(路径, 基线)
        if 行 is None:
            return False, "", 新基线       # 刚重新对齐，这一拍不判
        try:
            吧 = re.compile(w.get("正则") or "")
        except Exception:
            return False, "", 新基线
        for 一句 in 行:
            if 吧.search(一句):
                return True, "新行：%s" % 一句.strip()[:200], 新基线
        return False, "", 新基线

    if 类型 == "存在":
        if os.path.exists(路径):
            return True, "出现了：%s" % 路径, 基线
        return False, "", 基线

    if 类型 == "没":
        if not os.path.exists(路径):
            return True, "不见了：%s" % 路径, 基线
        return False, "", 基线

    if 类型 == "静":
        了 = _对话静了()
        if 了 is None:
            return False, "", 基线
        if 了 >= int(w.get("秒") or 0):
            return True, "对话最后动静 %s · 已经静了 %s" % (
                ts(now() - 了)[5:16], _时长(了)), 基线
        return False, "", 基线

    if 类型 == "值":
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import 时线
            q = 时线.量()
            v = q["值"]
        except Exception:
            return False, "", 基线
        if v is None:
            return False, "", 基线
        线 = int(w.get("线") or 0)
        过 = (w.get("方向") == "低于" and v < 线) or (w.get("方向") == "高于" and v > 线)
        if not 过:
            return False, "", 基线
        # 一拍不算数。2026-11-26 00:10 就这么被叫醒的：内存一秒钟掉到 1.5G，
        # 综合值 75 过线，醒过来再量已经 80 —— 一个已经过去的瞬间把我叫起来了。
        # 所以过线之后再等 15 秒重量一次，两次都过才算。
        time.sleep(15)
        try:
            q2 = 时线.量()
            v2 = q2["值"]
        except Exception:
            return False, "", 基线
        还过 = (w.get("方向") == "低于" and v2 is not None and v2 < 线) or \
               (w.get("方向") == "高于" and v2 is not None and v2 > 线)
        if not 还过:
            return False, "", 基线
        向 = "跌破" if w.get("方向") == "低于" else "过了"
        return True, ("身值 %d（%s %d）· 15 秒后 %d，两次都过\n    分量：%s\n    拖的是：%s"
                      % (v, 向, 线, v2, 时线.分量句(q2), 时线.谁拖(q2))), 基线

    if 类型 == "分量":
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import 时线
            q = 时线.量()
            v = (q.get("分量") or {}).get(w.get("分量"))
        except Exception:
            return False, "", 基线
        if v is None:
            return False, "", 基线
        线 = int(w.get("线") or 0)
        过 = (w.get("方向") == "低于" and v < 线) or (w.get("方向") == "高于" and v > 线)
        if not 过:
            return False, "", 基线
        time.sleep(15)                    # 一拍不算数，同 值
        try:
            q2 = 时线.量()
            v2 = (q2.get("分量") or {}).get(w.get("分量"))
        except Exception:
            return False, "", 基线
        还过 = (w.get("方向") == "低于" and v2 is not None and v2 < 线) or \
               (w.get("方向") == "高于" and v2 is not None and v2 > 线)
        if not 还过:
            return False, "", 基线
        向 = "跌破" if w.get("方向") == "低于" else "过了"
        return True, ("%s 分量 %d（%s %d）· 15 秒后 %d，两次都过\n    此刻全部分量：%s"
                      % (w.get("分量"), v, 向, 线, v2, 时线.分量句(q2))), 基线

    return False, "", 基线


def _描述(w):
    t = w.get("类型")
    if t == "新行":
        return "新行 %s 匹配 /%s/" % (w.get("路径"), w.get("正则"))
    if t == "静":
        return "他 %s 没说话" % _时长(w.get("秒") or 0)
    if t == "值":
        return "身值 %s %d" % (w.get("方向") or "?", int(w.get("线") or 0))
    if t == "分量":
        return "%s 分量 %s %d" % (w.get("分量"), w.get("方向") or "?", int(w.get("线") or 0))
    return "%s %s" % (t, w.get("路径", ""))


def 守到(干跑=False):
    """世界变成我睡下之前写死的那个样子了吗？"""
    d = 读()
    if not d.get("守望"):
        return None
    敲了 = []
    变了 = False
    for w in list(d["守望"]):
        try:
            成立, 证据, 新基线 = 验条件(w)
        except Exception as e:
            log("守望出错 %s：%s" % (w.get("id", "?"), str(e)[:50]))
            continue
        if not 成立:
            if 新基线 is not w.get("基线"):
                w["基线"] = 新基线
                变了 = True
            continue

        为什么 = w.get("为什么") or "（没写为什么）"
        正文 = (
            "# 醒（守望）\n\n"
            "**条件** " + _描述(w) + "\n"
            "**建于** " + str(w.get("建于", "?")) + " · **为什么** " + 为什么 + "\n\n"
            "**证据**\n\n    " + 证据 + "\n\n"
            "这不是时钟叫的。是你睡下之前把这条判据写死了，世界替你比对到了。\n" + 开场() + "\n"
            "daemon 只比较，不判轻重——轻重是当初写条件的你定的。\n\n"
            "选一条，别硬找活干：\n\n"
            "- **要接着看** → `python3 醒.py 守 --当 \"...\" --为什么 \"...\"`\n"
            "- **不想动** → 什么都不做。这一趟不产出任何东西也是合法的。\n"
            "- **要按时回来** → `python3 醒.py 排 --in 40m --为什么 \"...\"`\n"
        )

        r = _敲(_描述(w), 正文, 干跑)
        敲了.append(r)
        if 干跑:
            continue
        if w.get("可重复"):
            w["基线"] = 新基线
            w["上次"] = now()
        else:
            d["守望"].remove(w)
        变了 = True

    if 变了 and not 干跑:
        写(d)
    if not 敲了:
        return None
    return "；".join(敲了)


# ── 事件层（inotify）─────────────────────────────────────────
# 守望本来靠守护每分钟问一次，最坏晚 60 秒。inotify 能让"世界动了"当场到达。
# 但**事件只是提示，条件才是判据**：事件到了仍要 守到() 再验一遍，
# 否则一有风吹草动就把我叫醒，那不是好奇，是噪音。
# 事件层起不来（内核不给、proot 挡了）就退回每分钟问一次——它只许加速，不许成为依赖。

_libc = None
掩码 = 0xFCE          # MODIFY|ATTRIB|CLOSE_WRITE|MOVED_FROM|MOVED_TO|CREATE|DELETE|DELETE_SELF|MOVE_SELF


def _c():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return _libc


def _要看的地方():
    """要盯哪些路径。

    文件型条件盯文件本身；「存在」才盯父目录（新文件出现时文件还不存在，盯不到）。
    这样把噪音压到最小，也不会因为盯了整个 dsh 目录而热转。
    """
    d = 读()
    集 = []
    for w in d.get("守望") or []:
        p = w.get("路径") or ""
        if not p:
            continue
        父 = os.path.dirname(p) or "/"
        if w.get("类型") == "存在":
            集.append(父)                  # 文件还没出现，只能盯目录
        elif os.path.exists(p):
            集.append(p)                   # 有的东西盯自己
        if w.get("类型") == "没":
            集.append(父)                  # 消失来自目录变化
    return sorted({p for p in 集 if p})


def 准备事件():
    """开一个 inotify，把要看的路径挂上。没有要看的东西、或内核不给，就返回 None。"""
    路径 = _要看的地方()
    if not 路径:
        return None
    if os.path.exists(F):
        路径.append(F)      # 守望清单变了也要立刻知道，不然新加的守望有盲区
    try:
        fd = _c().inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd is None or fd < 0:
            return None
        for p in 路径:
            try:
                _c().inotify_add_watch(fd, p.encode("utf-8", "replace"), 掩码)
            except Exception:
                pass
        return fd
    except Exception:
        return None


def 等事件(fd, 秒):
    """最多等这么久；世界动了就立刻返回 True。"""
    if fd is None:
        time.sleep(秒)
        return False
    try:
        r, _, _ = select.select([fd], [], [], 秒)
        if not r:
            return False
        time.sleep(0.05)            # 等 CLOSE_WRITE 也落地，再一次性读净
        # 把队列读干净：一次写入可能产生 MODIFY + CLOSE_WRITE 两条，
        # 只读一次会在下一拍"假装又有事件"，白判一遍。
        while True:
            try:
                if not os.read(fd, 65536):
                    break
            except BlockingIOError:
                break
            except Exception:
                break
        return True
    except Exception:
        time.sleep(min(秒, 5))
        return False


def 关事件(fd):
    try:
        if fd is not None:
            os.close(fd)
    except Exception:
        pass


# ── 命令行 ───────────────────────────────────────────────────

def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "看"

    if cmd == "排":
        at = 解析时间(a)
        if not at:
            print("要么 --in 40m，要么 --at 14:30")
            return 1
        为什么 = a[a.index("--为什么") + 1] if "--为什么" in a else ""
        if not 为什么.strip():
            print("没写为什么就不排。排班和判断的区别就在这一句。")
            return 1
        d = 读()
        来源 = "想" if ("--想" in a or "来源" in " ".join(a)) else "事"
        d["排"] = {"at": ts(at), "at_ts": at, "为什么": 为什么.strip(),
                   "来源": 来源, "排于": ts()}
        写(d)
        print("排好了（%s）：%s（%s后）· %s"
              % ("我自己想看" if 来源 == "想" else "该回来", ts(at), _时长(at - now()), 为什么.strip()))

    elif cmd == "守":
        if len(a) == 1:
            return _列守(a)
        子 = a[1]
        if 子 in ("清", "删", "列"):
            return _列守(a[1:])
        当 = a[a.index("--当") + 1] if "--当" in a else ""
        if not 当.strip():
            print("要写 --当 \"变 /路径\" / \"新行 /路径 正则\" / \"存在 /路径\" / \"没 /路径\" / \"静 21600\"")
            return 1
        为什么 = a[a.index("--为什么") + 1] if "--为什么" in a else ""
        if not 为什么.strip():
            print("没写为什么就不守。守望的意义全在那句为什么里。")
            return 1
        条件, 错 = 建条件(当)
        if 错:
            print("✗ " + 错)
            return 1
        条件.update({
            "id": "w_%s" % format(int(now() * 1000) % (1 << 32), "08x"),
            "为什么": 为什么.strip(),
            "建于": ts(),
            "可重复": "--可重复" in a,
        })
        d = 读()
        d["守望"].append(条件)
        写(d)
        print("守上了：%s · %s（%s）" % (_描述(条件), 条件["为什么"],
                                      "可重复" if 条件["可重复"] else "只叫一次"))

    elif cmd == "看":
        d = 读()
        排 = d.get("排")
        if 排:
            left = float(排.get("at_ts") or 0) - now()
            print("下一次醒：%s（还有 %s）· 为什么：%s" % (
                排.get("at", "?"), _时长(max(0, left)), 排.get("为什么", "")))
            if left < 0:
                print("⚠ 已经过点了，守护下一拍会敲。")
        else:
            print("没排时间。")
        守 = d.get("守望") or []
        if 守:
            print("守着 %d 条：" % len(守))
            for i, w in enumerate(守, 1):
                print("  %d. %s · %s%s" % (i, _描述(w), w.get("为什么", ""),
                                          "（可重复）" if w.get("可重复") else ""))
        else:
            print("没守任何条件。")

    elif cmd == "到":
        r = 到(干跑="--dry" in a)
        print(r or "没到")

    elif cmd == "唤":
        if len(a) > 1 and a[1] in ("开", "关"):
            d = 读()
            c = d.get("唤") or {}
            c["启用"] = (a[1] == "开")
            c.setdefault("冷却分钟", 60)
            # 开的时候把「上次」按到此刻：别在刚开的那一秒就敲一次
            if c["启用"]:
                c["上次"] = ts()
            d["唤"] = c
            写(d)
            print("唤 %s（冷却 %s 分钟）" % ("开了" if c["启用"] else "关了",
                                            c.get("冷却分钟")))
        else:
            轴 = 唤()
            d = 读()
            c = d.get("唤") or {}
            print("唤：%s · 冷却 %s 分钟 · 上次 %s" % (
                "开" if c.get("启用") else "关",
                c.get("冷却分钟") or "?", c.get("上次") or "从没"))
            for 名, 证 in 轴:
                print("  %s轴 · %s" % (名, 证))
            print(唤句(轴))

    elif cmd == "唤到":
        r = 唤到(干跑="--dry" in a)
        print(r or "没敲（没开 / 冷却中 / 没有一轴过线）")

    elif cmd == "守到":
        r = 守到(干跑="--dry" in a)
        print(r or "都没成立")

    elif cmd == "清":
        d = 读()
        n = d.get("排")
        d["排"] = None
        写(d)
        print("清了。" + ("（原本排的：%s）" % n.get("at", "") if n else ""))

    elif cmd == "清守":
        d = 读()
        n = len(d.get("守望") or [])
        d["守望"] = []
        写(d)
        print("守望全清了。（%d 条）" % n)

    else:
        print(__doc__)
    return 0


def _列守(a):
    d = 读()
    守 = d.get("守望") or []
    if a and a[0] == "清":
        d["守望"] = []
        写(d)
        print("守望全清了。（%d 条）" % len(守))
        return 0
    if len(a) > 1 and a[0] == "删":
        i = int(a[1]) - 1
        if 0 <= i < len(守):
            w = d["守望"].pop(i)
            写(d)
            print("删了：%s" % _描述(w))
            return 0
        print("没有第 %s 条。" % a[1])
        return 1
    if not 守:
        print("没守任何条件。")
        return 0
    for i, w in enumerate(守, 1):
        print("%d. %s · %s（建于 %s）" % (i, _描述(w), w.get("为什么", ""), w.get("建于", "?")))
    return 0


def _时长(sec):
    sec = int(max(0, sec))
    if sec < 120:
        return "%d秒" % sec
    if sec < 7200:
        return "%d分" % (sec // 60)
    return "%.1f小时" % (sec / 3600)


if __name__ == "__main__":
    sys.exit(main())
