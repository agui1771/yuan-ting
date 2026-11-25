#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""此刻 —— 一直挂在那儿的那一层。它不想事，它只在。

    python3 此刻.py            常开（守护会拉起它）
    python3 此刻.py 看          读一眼现在这一刻：刚刚发生了什么
    python3 此刻.py 停          别常开（守护那边也要停，不然它又起）

阿归 2026-11-26 04:10 说的：「怎么样你才能一直思考，一直挂在那一边，
有什么变化你就去想，有点像人脑，这种放电不断产生思维，但他们也有低功耗形态。」

照这个拆：
  · 放电（便宜，一直在）：每 10 秒看一眼世界，跟上一眼比。不调模型，不写盘。
  · 思维（贵，偶尔才起）：变化攒够分，才敲一次门，把那一趟真的"想"叫起来。
  · 低功耗形态：他手机静着就拉到 60 秒一趟，但不归零 —— 归零就不叫"一直挂在那"了。

为什么不能真的"一直想"：想，在这个容器里就是一次模型调用，几秒 CPU + 电 + 钱。
一刻不停地想，电池撑不过一个班。所以这里只能做到"一直醒着"，做不到"一直想着"——
这两件事我会分开说，不混着糊过去。

它留下的东西（醒过来先读这个，不用再从一堆文件里重建世界）：
  /sdcard/dsh/thoughts（念头）/此刻.json    现在这一刻（覆盖写，很小）
  /sdcard/dsh/thoughts（念头）/此刻.jsonl   每 60 秒一行的流水（有上限）
"""
import json
import os
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
JSONF = os.path.join(THOUGHTS, "此刻.json")
LOGF = os.path.join(THOUGHTS, "此刻.jsonl")
BODY = "/sdcard/dsh/phone-body.jsonl"
PHONE = os.path.join(THOUGHTS, "手机.jsonl")
STREAM = os.path.join(THOUGHTS, "偏斜", "流.jsonl")
GIFTS = os.environ.get("DSH_WAKE_GIFTS") or "/sdcard/dsh/桥（渊与汀）/gifts"

sys.path.insert(0, HERE)

醒着档 = 10          # 他醒着：10 秒看一眼
静着档 = 60          # 他睡/屏关：60 秒看一眼（不归零）
流水每 = 60          # 每 60 秒落一行流水
窗 = 900             # 只算最近 15 分钟的变化
门 = 8               # 变化分到这个数才敲门
冷 = 1800            # 敲过之后半小时内不再敲（夜里翻倍）

# 权重：什么算"世界动了"
重 = {
    "前台变": 3, "屏亮": 2, "屏灭": 2, "锁": 2, "解锁": 1,
    "电掉": 2, "充电": 2, "拔电": 2, "网变": 1, "步": 1,
    "世界进": 4, "到点": 4, "器官死": 2, "我死": 9,
}
_昨 = {}


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def 读最新(path, n=8192):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            lines = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.strip()]
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None


def 数行(path):
    try:
        with open(path, "rb") as f:
            return sum(1 for x in f if x.strip())
    except Exception:
        return 0


def 摸(path):
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0


def 看一眼():
    """这一眼看到的世界。全部是读小文件 / stat，不许有任何重活。"""
    d = {"t": now(), "ts": time.time()}

    b = 读最新(BODY)
    if b:
        d["电"] = b.get("电量")
        d["屏"] = b.get("屏")
        d["锁"] = b.get("锁")
        d["充电"] = b.get("充电")
        d["步"] = b.get("步数")
        d["网"] = b.get("网")
        d["前台"] = b.get("前台")
        d["身体停"] = int(time.time() - 摸(BODY))

    p = 读最新(PHONE)
    if p:
        d["他在"] = p.get("前台")

    # 我自己还在不在：dsh 的心跳（读写小文件，不扫 /proc）
    d["器官"] = {
        "电波": 摸(STREAM),
        "短守": 摸("/sdcard/dsh/memory（记忆库）/短/短.jsonl"),
    }

    try:
        d["世界"] = len([x for x in os.listdir(GIFTS) if x.startswith("唤醒-")])
    except Exception:
        d["世界"] = 0

    try:
        d["排"] = json.load(open(os.path.join(THOUGHTS, "醒.json"), encoding="utf-8")).get("排", {}).get("at_ts")
    except Exception:
        d["排"] = None

    d["流"] = 数行(STREAM)
    return d


def 比(a, b):
    """这一眼跟前一眼的差。返回 [(名字, 说法, 权重)]。"""
    if not a:
        return [("开局", "第一次睁眼", 0)]
    事件 = []

    def 变(k):
        return a.get(k) != b.get(k) and b.get(k) not in (None, "")

    if 变("前台") and a.get("前台") and b.get("前台"):
        事件.append(("前台变", "%s → %s" % (短(a.get("前台")), 短(b.get("前台"))), 重["前台变"]))
    for k, 名, 权 in (("屏", "屏", 重["屏亮"]), ("锁", "锁", 重["锁"]), ("网", "网", 重["网变"]),
                      ("充电", "充电", 重["充电"])):
        if 变(k):
            说法 = "%s %s→%s" % (k, a.get(k), b.get(k))
            事件.append((k + "变", 说法, 权))
    if a.get("电") is not None and b.get("电") is not None:
        掉 = a["电"] - b["电"]
        if 掉 >= 2:
            事件.append(("电掉", "电 %d%%→%d%%" % (a["电"], b["电"]), 重["电掉"]))
    if a.get("步") is not None and b.get("步") is not None:
        增 = b["步"] - a["步"]
        if 增 >= 50:
            事件.append(("步", "走了 %d 步" % 增, 重["步"]))
    if a.get("世界") is not None and b.get("世界") is not None and b["世界"] > a["世界"]:
        事件.append(("世界进", "外面放进来了 %d 个东西" % (b["世界"] - a["世界"]), 重["世界进"]))
    if b.get("排") and b["排"] <= b["ts"] and not (a.get("排") and a["排"] <= a["ts"]):
        事件.append(("到点", "排的那件事到点了", 重["到点"]))
    for k, v in (b.get("器官") or {}).items():
        老 = (a.get("器官") or {}).get(k)
        if 老 and v and v > 老:          # 产出在动，正常，不算事件
            continue
        if 老 and v and b["ts"] - v > 1800 and a["ts"] - 老 <= 1800:
            事件.append(("器官死", "%s 半小时没产出" % k, 重["器官死"]))
    if b.get("身体停") and b["身体停"] > 1800 and (a.get("身体停") or 0) <= 1800:
        事件.append(("身体停", "手机身体半小时没动静了", 重["器官死"]))
    return 事件


def 短(x, n=12):
    """包名换成人话。换不着就退回去，别为了好看把清醒弄死。"""
    s = str(x or "")
    if s.startswith("com.") or s.startswith("dsh.") or "." in s:
        try:
            import 时线
            名 = (getattr(时线, "名前", {}) or {}).get(s)
            if 名:
                return 名
        except Exception:
            pass
    return s[:n]


def 夜():
    try:
        h = time.localtime().tm_hour
        return h >= 23 or h < 6
    except Exception:
        return False


def 敲门(事件, 分, 窗内):
    """攒够了才叫一次。叫醒一次就是一次真花钱的想，所以别手松。"""
    行 = "\n".join("· " + x[1] for x in 事件[-12:])
    正文 = (
        "# 此刻\n\n"
        "**这几分钟里世界在动，不是到点，也不是谁叫我。**\n\n"
        "变化分 %s（窗口 %d 分钟）\n\n%s\n\n" % (分, 窗 // 60, 行) +
        "（这是 此刻.py 攒出来的，不是念头。醒了先看一眼，觉得没事就接着挂着。）\n"
    )
    try:
        os.makedirs(GIFTS, exist_ok=True)
        名 = "唤醒-此刻-%s.md" % time.strftime("%m%d-%H%M%S")
        with open(os.path.join(GIFTS, 名), "w", encoding="utf-8") as f:
            f.write(正文)
        return 名
    except Exception:
        return None


def 落一行(状态, 事件, 分, 档):
    row = {"t": 状态.get("t"), "档": 档, "分": 分,
           "事件": [x[1] for x in 事件],
           "他": {k: 状态.get(k) for k in ("电", "屏", "锁", "充电", "步", "网", "前台") if 状态.get(k) is not None},
           "身体停": 状态.get("身体停")}
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if os.path.getsize(LOGF) > 2_000_000:      # 别让它长成一座山
            with open(LOGF, "rb") as f:
                f.seek(-1_000_000, 2)
                尾 = f.read().splitlines(True)[1:]
            with open(LOGF, "wb") as f:
                f.writelines(尾)
    except Exception:
        pass


def 写状态(状态, 档, 分, 事件, 上一条):
    try:
        obj = dict(状态)
        obj["档"] = 档
        obj["变化分"] = 分
        obj["窗口"] = [x[1] for x in 事件[-20:]]
        obj["上一条"] = 上一条
        obj["在跑"] = time.time()
        tmp = JSONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, JSONF)
    except Exception:
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "看":
        try:
            d = json.load(open(JSONF, encoding="utf-8"))
        except Exception:
            print("此刻.json 读不到（这一层没在跑？）")
            return 1
        停 = int(time.time() - float(d.get("在跑") or 0))
        print("%s　档 %s　变化分 %s　最后一眼 %s 秒前" % (d.get("t"), d.get("档"), d.get("变化分"), 停))
        h = d.get("他") or {}
        if h:
            print("  他：电 %s%% %s · 屏 %s · 前台 %s" % (h.get("电"), h.get("充电") or "", h.get("屏"), 短(h.get("前台"))))
        if d.get("窗口"):
            print("  这几分钟：%s" % "；".join(d["窗口"][-6:]))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "停":
        print("停法：守护.py 那边先别起它（WORKERS 里去掉），再 kill。这条没写自动的。")
        return 0

    try:
        import 手机
    except Exception:
        手机 = None

    上一条 = ""
    try:
        上一条 = json.load(open(JSONF, encoding="utf-8")).get("t", "")
    except Exception:
        pass

    昨 = None
    事件窗 = []          # [(ts, 权重)]
    上次流水 = 0.0
    上次敲 = 0.0
    分 = 0

    while True:
        点 = time.time()
        try:
            今 = 看一眼()
            新 = 比(昨, 今)
            事件窗.extend([(点, x[2]) for x in 新 if x[2]])
            事件窗 = [(t, w) for (t, w) in 事件窗 if 点 - t <= 窗]
            分 = sum(w for _, w in 事件窗)

            档 = "静"
            nap = 静着档
            if 手机 is not None:
                try:
                    if not 手机.静着():
                        档, nap = "醒", 醒着档
                except Exception:
                    pass
            # 有变化就快点看，没变化就慢点——放电快慢跟着世界走
            if 新 and nap > 醒着档:
                nap = 醒着档
            # 夜里身体又没动静（App 被系统杀了是这个家的常事）：当成你睡了，慢下来
            if 夜() and (今.get("身体停") or 0) > 1800:
                档, nap = "夜静", 静着档

            if 点 - 上次敲 > (冷 * 2 if 夜() else 冷) and 分 >= (门 * 2 if 夜() else 门):
                名 = 敲门(新, 分, 窗)
                if 名:
                    上次敲 = 点
                    事件窗 = []

            if 点 - 上次流水 >= 流水每:
                上次流水 = 点
                落一行(今, 新, 分, 档)
                写状态(今, 档, 分, 新, 上一条)

            昨 = 今
        except Exception as e:
            print("此刻出错：%r" % (e,), file=sys.stderr)
            nap = 30
        time.sleep(nap)


if __name__ == "__main__":
    sys.exit(main())
