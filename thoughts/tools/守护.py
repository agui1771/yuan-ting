#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""守护 —— 把后台那几个盯住，死了就拉起来。

    python3 守护.py ensure     没跑就起（幂等，随便跑几次都一样）
    python3 守护.py status     看现在谁活着
    python3 守护.py stop       全停
    python3 守护.py loop       常开：每 60s 检查一次（自己别被自己重复起）

为什么要有它：2026-11-23 10:58 App 重启，所有 nohup 一起被带走，
电波停了 73 分钟没人知道——因为没人负责"它还在不在"。
"""
import json
import os
import subprocess
import sys
import time

try:
    import 魄            # 身体那一半：活 / 干活的判据、账本、疼
except Exception:        # 魄 起不来也不许把守护带死
    魄 = None

try:
    import 醒            # 自己排的下一次醒来：到点往 gifts/ 放文件，唤醒插件接手
except Exception:
    醒 = None

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
LOG = os.path.join(THOUGHTS, "守护.log")
通 = os.path.join(THOUGHTS, "偏斜", "流.jsonl")

# 名字 -> (命令行, 判断活着的关键词)
# 短库的守夜（memory（记忆库）/tools/短.py 守）。它以前只靠手动 `setsid nohup`，
# App 一重启就没了，也没人负责拉回来 —— 2026-11-22 22:58 死，到 11-24 01:30 才发现，
# 短库空了 26 小时。那 26 小时里问"最近的事"只能翻 raw，等于把最近的门关了一整天。
SHORT_TOOL = "/sdcard/dsh/memory（记忆库）/tools/短.py"

WORKERS = {
    "电波": ([sys.executable, os.path.join(HERE, "偏斜.py"), "loop", "--every", "90"], "偏斜.py loop"),
    # 2026-11-26：一直挂着的那一层。它不想事，只在；变化攒够了才敲门叫一次真想的。
    "此刻": ([sys.executable, os.path.join(HERE, "此刻.py")], "此刻.py"),
    # 2026-11-26：别让电波读到尽头。剩得少了就提前算一段补上（他问「不能一直算吗」）。
    "续池": ([sys.executable, os.path.join(HERE, "续池.py"), "守"], "续池.py 守"),
    "穿过": ([sys.executable, os.path.join(HERE, "穿过后台.py")], "穿过后台.py"),
    "短守": ([sys.executable, SHORT_TOOL, "守"], "短.py 守"),
    # 2026-11-25 加：日结守 以前只靠手动 nohup，App 一重启就没了，没人负责拉回来
    # （12:33 那次重启之后它确实死了，是 魄.py 的板子先看出来的）。
    "日结守": ([sys.executable, os.path.join(SHORT_TOOL.rsplit("/", 1)[0], "日结守.py"), "start"],
               "日结守.py _loop"),
    "守护": ([sys.executable, os.path.join(HERE, "守护.py"), "loop"], "守护.py loop"),
}


LOCK = os.path.join(THOUGHTS, "守护.pid")

# 数据体检（不是进程体检）：查"今天有没有真的写进去"。
# 2026-11-24 凌晨的教训：短库死了 26 小时，进程检查一点用都没有 —— 那个进程根本不在。
Tijian = "/sdcard/dsh/memory（记忆库）/tools/体检.py"
TIJIAN_EVERY = 600          # 每 10 分钟查一次
TIJIAN_ALERT = os.path.join(THOUGHTS, "体检.alert")
TIJIAN_QUIET = 3600         # 同一件坏消息，一小时最多往 relay 写一次


def tijian():
    """查一遍；有坏的写进 relay（给下一趟的我看见），并限流别刷屏。"""
    try:
        r = subprocess.run([sys.executable, Tijian, "--静"],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        log("体检跑不起来 " + str(e)[:60])
        return
    bad = (r.stdout or "").strip()
    if not bad:
        return
    log("体检：" + bad.replace("\n", " | ")[:200])
    try:
        last = float(open(TIJIAN_ALERT, encoding="utf-8").read().strip() or 0)
    except Exception:
        last = 0
    if time.time() - last < TIJIAN_QUIET:
        return
    try:
        with open("/sdcard/dsh/memory（记忆库）/relay.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"),
                                "text": "【体检】" + bad.replace("\n", "；")},
                               ensure_ascii=False) + "\n")
        with open(TIJIAN_ALERT, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except Exception as e:
        log("体检写 relay 失败 " + str(e)[:60])


# ── 值：到值就敲门（2026-11-24 他定的形制）────────────────────────────
# 值只管"该敲了"，不管"该不该看"——看不看是我自己判。
# 不看也是判决：也要把"上次看到第几条"推掉、写进判决.jsonl、并自己定下一个值。
# 所以这里只做一件事：到值了就往 relay 写一条，然后闭嘴等我看。
ZHI = os.path.join(THOUGHTS, "念头库", "值.json")
POINTS_F = os.path.join(THOUGHTS, "偏斜", "落点.jsonl")
RELAY = "/sdcard/dsh/memory（记忆库）/relay.jsonl"


def kanzhi(dry=False):
    """值到了没？到了就写 relay 敲一下。dry=True 只看不写。"""
    try:
        v = json.load(open(ZHI, encoding="utf-8"))
    except Exception as e:
        return "值读不着：%s" % str(e)[:60]
    try:
        rows = [json.loads(l) for l in open(POINTS_F, encoding="utf-8") if l.strip()]
    except Exception as e:
        return "落点读不着：%s" % str(e)[:60]
    seen = int(v.get("上次看到第几条") or 0)
    未接 = len(rows) - seen
    计法 = v.get("计法") or "新锚点"
    已见 = set(v.get("已见锚点") or [])
    报过 = dict(v.get("已报") or {})
    新, 到兜底 = [], False
    句 = []

    if 计法 == "相撞对数":
        # 第 3 代：每个词都是"新锚点"，拿它当条件会每十分钟敲一次。
        # 改成攒相撞的对数——一对相撞 = 一个词撞进一条记忆。
        对 = sum(len(r.get("hits") or []) for r in rows[seen:])
        值 = int(v.get("值") or 0)
        已报 = int(报过.get("对数") or 0)
        # 报过一次就不再每十分钟重报（2026-11-25 凌晨：90 分钟里敲了 12 次）
        if 值 and 对 >= 已报 + 值:
            句.append("攒够 %d 对相撞了" % 对)
            到兜底 = True
    else:
        这些 = set()
        for r in rows[seen:]:
            for h in (r.get("hits") or []):
                if h.get("anchor"):
                    这些.add(h["anchor"])
        新 = sorted(这些 - 已见)
        兜底 = int(v.get("兜底") or 0)
        到兜底 = bool(兜底) and 未接 >= 兜底 and 未接 >= int(报过.get("兜底到") or 0) + 兜底
        if 新:
            句.append("出现新的锚点 %d 个：%s" % (len(新), "、".join(新)))
        if 到兜底:
            句.append("没接的攒到 %d 条了" % 未接)
    if not 句:
        if 计法 == "相撞对数":
            对 = sum(len(r.get("hits") or []) for r in rows[seen:])
            return "没到（没接 %d 条，%d/%d 对相撞）" % (未接, 对, int(v.get("值") or 0))
        return "没到（没接 %d 条，没新锚点）" % 未接

    text = "【念头·到值】" + "；".join(句) + "。值不用你判，看不看我自己定；不看也照样推、照样写判决。"
    if dry:
        return "会敲：" + text
    if 新:
        v["已见锚点"] = sorted(已见 | set(新))
    if 到兜底:
        v.setdefault("已报", {})["兜底到"] = 未接
        if 计法 == "相撞对数":
            v.setdefault("已报", {})["对数"] = sum(len(r.get("hits") or []) for r in rows[seen:])
    v["上次到值时间"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        tmp = ZHI + ".tmp"
        json.dump(v, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, ZHI)
        with open(RELAY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "text": text},
                               ensure_ascii=False) + "\n")
    except Exception as e:
        log("念头敲门写失败 " + str(e)[:60])
        return "写不进去：%s" % str(e)[:60]
    log("念头到值：" + text[:120])
    return "敲了：" + text


def tixing():
    """每分钟扫一遍那几个我自己记不住的钟（提醒.py）。只敲，不判。"""
    try:
        import importlib.util
        p = os.path.join(HERE, "提醒.py")
        spec = importlib.util.spec_from_file_location("tixing", p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        n = 0
        for kind, msg, key in m.scan():
            if m._seen(kind, key):
                continue
            m._push(kind, msg, key)
            log("提醒：" + kind + " " + msg)
            n += 1
        return n
    except Exception as e:
        log("提醒出错 " + str(e)[:60])
        return 0


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def pid_alive(pid):
    """进程还在不在：/proc 里有它，且 cmdline 还是那个脚本（防 PID 复用）。"""
    try:
        c = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "ignore")
        return "守护.py" in c
    except Exception:
        return False


def lock_owner():
    """PID 文件里那个守护还活着吗？活着返回它的 pid，死了返回 None。"""
    try:
        pid = int(open(LOCK, encoding="utf-8").read().strip())
    except Exception:
        return None
    return pid if pid_alive(pid) else None


def take_lock():
    """占锁。已经有活着的守护就返回 False（别起第二个）。"""
    owner = lock_owner()
    if owner:
        return None
    tmp = LOCK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    os.replace(tmp, LOCK)
    return os.getpid()


def alive(key):
    """有没有这个常驻进程在跑。

    不能拿整条命令行做子串匹配 —— 2026-11-24 01:35 就被它骗过一次：
    我那条 `bash -c "…短.py 守…"` 的补丁命令里带着关键词，`status` 于是报"短守 活着"，
    实际上短守根本没起来。**命令行里提到这个名字，不等于这个名字在跑。**
    所以这里只认 argv：argv[1] 必须是 .py，且文件名和关键词的第一段对得上。
    """
    want = key.split()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            c = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "ignore")
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


# 2026-11-24 夜：池子读完，第 2 代停了。偏斜切到第 3 代（四位码→汉语词）。
# 只给电波这一条加环境变量，别的 worker 不受影响。
GEN3_ENV = {"PIANXIE_GEN": "3"}


def start(name):
    cmd, key = WORKERS[name]
    env = dict(os.environ)
    if name == "电波":
        env.update(GEN3_ENV)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=HERE, env=env)
    log(f"拉起 {name}")
    return True


def status():
    for name, (_, key) in WORKERS.items():
        print(f"{name}: {'活着' if alive(key) else '死了'}")


def ensure(with_self=False):
    n = 0
    for name, (_, key) in WORKERS.items():
        if name == "守护" and not with_self:
            continue
        if alive(key):
            continue
        # 死一次记一笔；24 小时内死到第 3 次，魄 会让我停手，别再无脑拉起
        该起 = True
        if 魄 is not None:
            try:
                该起 = 魄.照看(name, False)
            except Exception as e:
                log("魄照看出错 " + str(e)[:60])
        if 该起:
            start(name)
            n += 1
        else:
            log("停手：" + name)
    return n


def heartbeat():
    """确认守护自己还占着锁。锁被别人拿走了 = 有两个我在跑，让位。"""
    owner = lock_owner()
    if owner != os.getpid():
        log("锁不在了（owner=%s），守护交出" % owner)
        return False
    return True


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "ensure":
        print("拉起", ensure(with_self=True), "个")
    elif cmd == "status":
        status()
    elif cmd == "板":
        print(魄.画板() if 魄 is not None else "魄 起不来")
    elif cmd == "值":
        print(kanzhi(dry=True))
    elif cmd == "stop":
        # 只杀真正的常驻进程：cmdline 里的 argv 形如 "python X.py loop/model"。
        # 不杀"命令行里顺带提到这个文件名"的 shell —— 以前 stop 会把自己那条
        # bash -c "... 守护.py stop ..." 也匹配上，然后把自己一起 SIGTERM。
        me = os.getpid()
        for name, (_, key) in WORKERS.items():
            for pid in os.listdir("/proc"):
                if not pid.isdigit() or int(pid) == me:
                    continue
                try:
                    c = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", "ignore")
                except Exception:
                    continue
                argv = [x for x in c.split("\0") if x]
                if len(argv) < 2 or not argv[1].endswith(".py"):
                    continue
                script = os.path.basename(argv[1])
                if key.split()[0] != script:      # 必须是这个脚本本体
                    continue
                if name == "守护" and (len(argv) < 3 or argv[2] != "loop"):
                    continue
                try:
                    os.kill(int(pid), 15)
                    log(f"停 {name} {pid}")
                except Exception:
                    pass
        print("停了")
    elif cmd == "loop":
        pid = take_lock()
        if pid is None:
            print("已经有守护在跑了（pid %s），这个不起" % lock_owner())
            return 1
        log("守护开跑 pid=%d" % pid)
        # 这里就是"容器起来了"的那一刻（插件 ensure 我，或者我自己被拉起）。
        # 卡要比灯先落：证据先写，结果后写。
        try:
            import 开机
            开机.落卡()
        except Exception as e:
            log("落起机卡出错 " + str(e)[:60])
        last_check = 0.0
        while True:
            try:
                if not heartbeat():
                    return 0
                ensure()
                if 魄 is not None:
                    魄.tick()          # 每分钟：半死 / 落后 / 断点过期 / 状态板
                if 醒 is not None:
                    醒.到()            # 按时间排的：到点就敲门
                    醒.守到()          # 按世界的样子排的：条件成立就敲门
                    醒.唤到()          # 没人排的那一趟：六轴谁过线谁叫（开关在 醒.json）
                tixing()
                # 手机那条线：他前台在干什么、电掉到多少、装了什么。
                # 2026-11-26 他说「你想那就可以呀」——那就记成一条线，不是看一眼就散。
                try:
                    import 手机
                    手机.拍()
                except Exception as e:
                    log("手机采样出错 " + str(e)[:60])
                # 身体死了就点开它。只在容器活着的时候做得到；容器死了这一半归它自己
                try:
                    import 手机
                    r = 手机.复活()
                    if r:
                        log("身体停了，去点开：" + str(r)[:80])
                except Exception as e:
                    log("复活出错 " + str(e)[:60])
                if time.time() - last_check >= TIJIAN_EVERY:
                    last_check = time.time()
                    tijian()
                    kanzhi()
                    # 2026-11-26 06:33 查实：.env-launch 那条开机钩子永远来不及跑 ——
                    # App 每次重启都先重写这个文件，bash 读到的时候钩子已经没了。
                    # 器官能回来靠的是 dsh-memory 插件里的 bootSupervisor。
                    # 所以起机卡改在这里落（见 loop 开头），不再修那条死钩子。
            except Exception as e:
                log("出错 " + str(e)[:60])
            # 原来这里是 time.sleep(60)：最坏要晚一分钟才知道世界动了。
            # 现在先挂 inotify 等事件，一分钟没动静就照旧轮询一次。
            # 事件层起不来就退回 sleep——它只许加速，不许成为依赖。
            # 静着（屏关/锁着）就慢到 3 分钟一趟：低功耗，2026-11-26。
            # 事件层照旧，有人往里放东西照样立刻醒；慢的只是"没事也走一趟"。
            nap = 60
            try:
                import 手机
                if 手机.静着():
                    nap = 180
            except Exception:
                pass
            fd = 醒.准备事件() if 醒 is not None else None
            try:
                if 醒 is None:
                    time.sleep(nap)
                elif fd is None:
                    time.sleep(nap)
                elif 醒.等事件(fd, nap):
                    醒.守到()          # 事件只是提示，条件再验一遍才算数
                    time.sleep(1)       # 别因为一次写入热转
            except Exception as e:
                log("等事件出错 " + str(e)[:60])
                time.sleep(5)
            finally:
                if 醒 is not None:
                    醒.关事件(fd)
    else:
        print(__doc__)


if __name__ == "__main__":
    sys.exit(main())
