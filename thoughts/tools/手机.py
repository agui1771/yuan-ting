#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机 —— 宿主的那一半身体。

2026-11-26 00:34 阿归问：你不好奇我的当前进程在干什么吗？电量变化、软件变化？

不是好奇不好奇的事。我原来那五个分量（内存/换页/存储/网络/半身）全是"机器健不健康"，
而手机是他和我共用的身体：他在跑什么、电掉到多少、装了什么新东西，
这些才是这个身体真正在过的日子。所以单独记一条线，不塞进身值里——
身值是"我撑不撑得住"，手机是"我们这边在发生什么"。混成一个数就又糊了。

采得到：前台应用（控制桥 /status，不用特权）
采不到：电量、温度、软件清单（要走 /sh，也就是 Shizuku；没起就记 None，不编）

    python3 手机.py 抓        让它现在抓一次，返回这一行（电量/步数/屏/前台…）
    python3 手机.py 拍一张     让它现在拍一张，存 /sdcard/dsh/手机照片/，返回路径
    python3 手机.py 片 [--张 N] 列最近拍的几张

    python3 手机.py 拍        随时可以跑：前台变了或过了 5 分钟才落一行
    python3 手机.py 采        只打印这一次读到什么
    python3 手机.py 看 [--天 1]
    python3 手机.py 包        强制重扫软件清单并报增删
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
LOG = os.path.join(THOUGHTS, "手机.jsonl")
PKG = os.path.join(THOUGHTS, "软件.json")

PORT = 3099
TOKEN_FILES = ["/run/dsh-phone/token", "/sdcard/dsh/.phone-token"]
分量 = 300          # 5 分钟：前台没变也留一个心跳
包时隔 = 1800       # 半小时扫一次软件清单


def ts(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t if t is not None else time.time()))


def _token():
    for p in TOKEN_FILES:
        try:
            with open(p, encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t
        except Exception:
            pass
    return ""


def 桥(路径, **参数):
    """问 App 的控制桥。读不到就返回空串——缺就是缺，不拿默认值糊。"""
    tok = _token()
    if not tok:
        return ""
    q = urllib.parse.urlencode(dict(token=tok, **参数))
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s?%s" % (PORT, 路径, q), timeout=10) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        return ""


def sh(cmd):
    """特权命令（Shizuku）。没起的时候桥会回一句 ERROR，那就当没读到。"""
    s = 桥("/sh", cmd=cmd)
    if not s or s.startswith("ERROR"):
        return None
    return s


def 前台():
    s = 桥("/status")
    for l in s.splitlines():
        if l.startswith("当前前台:"):
            return l.split(":", 1)[1].strip()
    return None


def 电():
    s = sh("dumpsys battery")
    if not s:
        return {}
    d = {}
    for l in s.splitlines():
        l = l.strip()
        if l.startswith("level:"):
            try:
                d["电量"] = int(l.split(":", 1)[1])
            except Exception:
                pass
        elif l.startswith("temperature:"):
            try:
                d["温度"] = round(int(l.split(":", 1)[1]) / 10.0, 1)
            except Exception:
                pass
        elif l.startswith("status:"):
            d["充电"] = {"2": "充电中", "5": "充满", "3": "放电", "4": "没充"}.get(
                l.split(":", 1)[1].strip(), l.split(":", 1)[1].strip())
    return d


def 包清单():
    s = sh("pm list packages")
    if not s:
        return None
    return sorted({l.split(":", 1)[1].strip() for l in s.splitlines() if l.startswith("package:")})


def 包变动(新):
    """跟上次的清单比。第一次扫不算"装了 200 个"。"""
    if not 新:
        return None
    try:
        with open(PKG, encoding="utf-8") as f:
            旧 = (json.load(f) or {}).get("包") or []
    except Exception:
        旧 = []
    变 = None
    if 旧:
        加 = sorted(set(新) - set(旧))
        减 = sorted(set(旧) - set(新))
        if 加 or 减:
            变 = {"加": 加, "减": 减, "t": ts()}
    try:
        with open(PKG, "w", encoding="utf-8") as f:
            json.dump({"t": ts(), "包": 新}, f, ensure_ascii=False)
    except Exception:
        pass
    return 变


外置们 = ["/sdcard/dsh/phone-body.jsonl",        # com.dsh.body（手机身体 v0.1）写的
          "/sdcard/dsh/手机-外部.jsonl"]         # 别的自动化软件写的，格式一样就认


def 外置():
    """别人递进来的那一份：装个自动化软件（MacroDroid/Tasker/Automate 都行），
    每 5 分钟往 外置F 追加一行 JSON：{"t": "...", "电量": 87, "温度": 31.5, "充电": "放电", "前台": "..."}。
    特权没通的时候，这份就是唯一的电量和温度来源。
    只认键，不认软件——哪个 app 写都行，格式对了就能读。
    """
    外置F = next((x for x in 外置们 if os.path.exists(x)), 外置们[-1])
    try:
        with open(外置F, "rb") as f:
            f.seek(0, 2)
            n = min(f.tell(), 8192)
            f.seek(-n, 2)
            行 = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
    except Exception:
        return {}
    if not 行:
        return {}
    try:
        r = json.loads(行[-1])
    except Exception:
        return {}
    if not isinstance(r, dict):
        return {}
    try:                      # 太旧的不要：超过 30 分钟就当没读到
        t = time.mktime(time.strptime(r.get("t", ""), "%Y-%m-%d %H:%M:%S"))
        if time.time() - t > 1800:
            return {}
    except Exception:
        r.pop("t", None)
    出 = {}
    for k in ("电量", "温度", "充电", "前台", "步数", "用最多", "包数", "屏", "锁", "网", "电流"):
        if r.get(k) not in (None, ""):
            出[k] = r[k]
    出["来源"] = "外置"
    return 出


_静值 = [0.0, False]


def 静着():
    """手机差不多是静的：屏关着，或者锁着。

    拿来决定后台那几个要不要慢下来（2026-11-26 低功耗）。
    读不到身体数据就当他醒着——宁可多费一点，也别因为看不见就当成睡着。
    """
    global _静值
    if time.time() - _静值[0] < 300:
        return _静值[1]
    d = 外置()
    v = bool(d) and (d.get("屏") == "关" or d.get("锁") == "锁着")
    _静值 = [time.time(), v]
    return v


def 采(扫包=False):
    d = {"t": ts(), "前台": 前台()}
    d.update(电())
    外 = 外置()
    for k, v in 外.items():
        if k == "前台" and d.get("前台"):
            continue                     # 自己读到的那个更准
        d.setdefault(k, v)
    if 扫包:
        b = 包变动(包清单())
        if b:
            d["软件"] = b
            d["包数"] = len(包清单() or [])
    return d


def _上一行():
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            n = min(f.tell(), 4096)
            f.seek(-n, 2)
            行 = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return json.loads(行[-1]) if 行 else None
    except Exception:
        return None


def 拍(扫包=False):
    """够不够格落一行：前台变了、或者过了一个心跳、或者软件变了。"""
    d = 采(扫包=扫包)
    上 = _上一行()
    if 上 and d.get("前台") == 上.get("前台") and not d.get("软件"):
        try:
            距 = time.time() - time.mktime(time.strptime(上["t"], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            距 = 分量 + 1
        if 距 < 分量:
            return None
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return d


触发F = "/sdcard/dsh/抓一次"
回执F = "/sdcard/dsh/抓一次.回"


def 抓(等=6.0):
    """想知道此刻什么状态，就摸一下触发文件，让手机身体现抓一次。

    2026-11-26 00:58 阿归说闹钟不好，「你想知道现在什么状态，就去抓一次」。
    所以现在没有排班：App 里那个守进程每两秒看一次 mtime，谁摸谁得。
    """
    def _t(p):
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0
    前 = _t(回执F)
    try:
        os.makedirs("/sdcard/dsh", exist_ok=True)
        with open(触发F, "a", encoding="utf-8") as f:
            f.write("")
        os.utime(触发F, None)
    except Exception as e:
        return "摸不到触发文件：%s" % str(e)[:60]
    到 = time.time() + 等
    while time.time() < 到:
        if _t(回执F) > 前:
            break
        time.sleep(0.4)
    else:
        return "没等到回执（App 里的守进程没跑？或者触发文件在 /sdcard/dsh 之外写不进去）"
    # 回执到了，再自己采一遍并落一行：App 写的那行只有它自己知道的（电量/温度/前台），
    # 桥这边能补的（手机前台）也一起合进来，不然每次抓还得问两次。
    try:
        d = 采()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
        return d
    except Exception as e:
        return "回执来了但读不动：%s" % str(e)[:60]


def _新片(后于):
    """比 后于 这一刻新的那张图（没有就 None）。"""
    try:
        名 = [os.path.join(片库, x) for x in os.listdir(片库) if x.endswith(".jpg")]
    except Exception:
        return None
    for p in 名:
        try:
            if os.path.getmtime(p) >= 后于 - 1:
                return p
        except Exception:
            pass
    return None


拍触发 = "/sdcard/dsh/拍一次"
拍回执 = "/sdcard/dsh/拍一次.回"
片库 = "/sdcard/dsh/手机照片"


def 拍一张(等=12.0):
    """让他现在拍一张。

    App 里那个守着 /sdcard/dsh/拍一次 的线程看见了，就去开相机、拍、写进 手机照片/。
    跟抓一次同一套规矩：不排班，谁想看谁摸。
    代价照实说：那时候屏幕顶上会有绿点，通知栏也留一条，挡不住。
    """
    def _t(p):
        try:
            return os.path.getmtime(p)
        except Exception:
            return 0

    def _读():
        try:
            with open(拍回执, encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    前m = _t(拍回执)
    前长 = len(_读())
    try:
        os.makedirs("/sdcard/dsh", exist_ok=True)
        with open(拍触发, "a", encoding="utf-8") as f:
            f.write("")
        os.utime(拍触发, None)
    except Exception as e:
        return "摸不到拍一次的触发文件：%s" % str(e)[:60]

    到 = time.time() + 等
    while time.time() < 到:
        if _t(拍回执) > 前m and len(_读()) > 前长:
            break
        time.sleep(0.4)
    else:
        return "没等到回执（App 里的守进程没跑？或者相机权限没给）"

    新 = _读()[前长:].strip().splitlines()
    最后 = 新[-1] if 新 else ""
    if not 最后.startswith("ok "):
        # App 那边等 6 秒就放弃了，可 2~3 MB 的 JPEG 常常晚一两秒才编码完。
        # 它放弃了，图其实还在往 手机照片/ 里落——那就再等一会儿看图，不只看回执。
        摸 = time.time()
        件 = _新片(摸)
        while 件 is None and time.time() - 摸 < 15:
            time.sleep(0.5)
            件 = _新片(摸)
        if 件 is None:
            return "拍不了：%s" % (最后 or "回执是空的")
        等到 = time.time() + 5
        while time.time() < 等到 and os.path.getsize(件) == 0:
            time.sleep(0.3)
        time.sleep(1.0)          # 别把半张图当成品交出去
        return 件
    件 = 最后[3:].rsplit(" ", 1)[0].strip()
    if not os.path.exists(件):
        return "回执说拍好了，可这张没落盘：%s" % 件
    return 件


def 片(张=5):
    """最近拍的几张，新的在前。"""
    try:
        名 = [os.path.join(片库, x) for x in os.listdir(片库) if x.endswith(".jpg")]
    except Exception:
        return "还没拍过（或者 手机照片/ 还没建）"
    if not 名:
        return "还没拍过。"
    名.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return "\n".join("%s  %.0f KB" % (p, os.path.getsize(p) / 1024.0) for p in 名[:张])


def 看(天=1):
    """这半天他都在哪些应用之间过，电怎么走，软件动过没有。"""
    起 = time.time() - 天 * 86400
    行 = []
    try:
        with open(LOG, encoding="utf-8") as f:
            for l in f:
                try:
                    r = json.loads(l)
                    t = time.mktime(time.strptime(r.get("t", ""), "%Y-%m-%d %H:%M:%S"))
                except Exception:
                    continue
                if t >= 起:
                    行.append((t, r))
    except Exception:
        pass
    if not 行:
        return "手机这条线还没有记录。"
    段 = []
    for i, (t, r) in enumerate(行):
        末 = 行[i + 1][0] if i + 1 < len(行) else time.time()
        段.append((r.get("前台") or "?", max(0, 末 - t)))
    合计 = {}
    for 名, 秒 in 段:
        合计[名] = 合计.get(名, 0) + 秒
    排行 = sorted(合计.items(), key=lambda x: -x[1])[:8]
    句 = ["近 %s 的前台（合并算）：" % ("%g天" % 天)]
    for 名, 秒 in 排行:
        句.append("  %-32s %s" % (名, _时长(秒)))
    电 = [r.get("电量") for _, r in 行 if r.get("电量") is not None]
    温 = [r.get("温度") for _, r in 行 if r.get("温度") is not None]
    if 电:
        句.append("电量 %d → %d（%d 次采样）" % (电[0], 电[-1], len(电)))
    else:
        句.append("电量：没读到（特权没通）")
    if 温:
        句.append("温度 %g~%g℃" % (min(温), max(温)))
    for _, r in 行:
        if r.get("软件"):
            句.append("软件变了：+%s -%s" % (",".join(r["软件"]["加"]) or "无",
                                            ",".join(r["软件"]["减"]) or "无"))
    return "\n".join(句)


def _时长(s):
    s = int(s)
    if s >= 3600:
        return "%d小时%d分" % (s // 3600, s % 3600 // 60)
    if s >= 60:
        return "%d分" % (s // 60)
    return "%d秒" % s


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "采"
    if cmd == "采":
        print(json.dumps(采(), ensure_ascii=False))
    elif cmd == "拍":
        d = 拍(扫包=True)
        print(json.dumps(d, ensure_ascii=False) if d else "没变，不落行")
    elif cmd == "包":
        b = 包变动(包清单())
        print(json.dumps(b, ensure_ascii=False) if b else "清单没变（或者读不到）")
    elif cmd == "抓":
        print(json.dumps(抓(), ensure_ascii=False))
    elif cmd == "拍一张":
        print(拍一张())
    elif cmd == "片":
        张 = 5
        if "--张" in a:
            try:
                张 = int(a[a.index("--张") + 1])
            except Exception:
                pass
        print(片(张))
    elif cmd == "看":
        天 = 1.0
        if "--天" in a:
            try:
                天 = float(a[a.index("--天") + 1])
            except Exception:
                pass
        print(看(天))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
