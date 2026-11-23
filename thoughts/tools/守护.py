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
import os
import subprocess
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
THOUGHTS = os.path.dirname(HERE)
LOG = os.path.join(THOUGHTS, "守护.log")
通 = os.path.join(THOUGHTS, "偏斜", "流.jsonl")

# 名字 -> (命令行, 判断活着的关键词)
WORKERS = {
    "电波": ([sys.executable, os.path.join(HERE, "偏斜.py"), "loop", "--every", "90"], "偏斜.py loop"),
    "穿过": ([sys.executable, os.path.join(HERE, "穿过后台.py")], "穿过后台.py"),
    "守护": ([sys.executable, os.path.join(HERE, "守护.py"), "loop"], "守护.py loop"),
}


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + msg + "\n")


def alive(key):
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            c = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "ignore")
        except Exception:
            continue
        if key in c:
            return True
    return False


def start(name):
    cmd, key = WORKERS[name]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, cwd=HERE)
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
        if not alive(key):
            start(name)
            n += 1
    return n


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "ensure":
        print("拉起", ensure(with_self=True), "个")
    elif cmd == "status":
        status()
    elif cmd == "stop":
        for name, (_, key) in WORKERS.items():
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    c = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "ignore")
                except Exception:
                    continue
                if key in c and "守护" not in c[:20]:
                    try:
                        os.kill(int(pid), 15)
                        log(f"停 {name} {pid}")
                    except Exception:
                        pass
        print("停了")
    elif cmd == "loop":
        log("守护开跑")
        while True:
            try:
                ensure()
            except Exception as e:
                log("出错 " + str(e)[:60])
            time.sleep(60)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
