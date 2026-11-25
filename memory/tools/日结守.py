#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""到点就跑日结程序.py --write 的小守护。不烧 token，不依赖会话在不在。

  python3 日结守.py start|stop|status
  默认每天 23:50 写当天那份；起来时会补写昨天漏掉的那份（只补一天）。
"""
import os, subprocess, sys, time
from datetime import datetime, timedelta, timezone
CN = timezone(timedelta(hours=8))

HERE = os.path.dirname(os.path.abspath(__file__))
PROG = os.path.join(HERE, "日结程序.py")
PIDF = os.path.join(HERE, ".日结守.pid")
LOG = os.path.join(HERE, ".日结守.log")
AT = (23, 50)
KAN = os.path.join("/sdcard/dsh/thoughts（念头）/tools", "看流.py")

def write(day):
    try:
        subprocess.run([sys.executable, PROG, day, "--write"], timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(LOG, "a") as f:
            f.write("%s 写出 %s\n" % (datetime.now(CN).strftime("%F %T"), day))
    except Exception as e:
        with open(LOG, "a") as f:
            f.write("%s 失败 %s %s\n" % (datetime.now(CN).strftime("%F %T"), day, e))

def kan(*args):
    try:
        subprocess.run([sys.executable, KAN] + list(args), timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        with open(LOG, "a") as f:
            f.write("%s 看流失败 %s\n" % (datetime.now(CN).strftime("%F %T"), e))


def loop():
    last = None
    last_check = None
    # 起来先补昨天（如果昨天那份不存在）
    y = (datetime.now() - timedelta(days=1)).strftime("%F")
    out = os.path.join(os.path.dirname(HERE), "短", "日结", y + ".程序.md")
    if not os.path.exists(out):
        write(y)
    while True:
        now = datetime.now(CN)
        # 每半小时看一次流（挂词一出就敲；爆量也敲）
        ck = now.strftime("%F %H:%M")
        if now.minute % 30 == 0 and last_check != ck:
            kan("--check")
            last_check = ck
        if (now.hour, now.minute) >= AT and last != now.strftime("%F"):
            write(now.strftime("%F"))
            kan("--day")
            last = now.strftime("%F")
        time.sleep(30)

def alive(pid):
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        if os.path.exists(PIDF):
            try:
                pid = int(open(PIDF).read().strip())
                if alive(pid):
                    print("已经在跑 pid", pid); return
            except Exception:
                pass
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "_loop"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        open(PIDF, "w").write(str(p.pid))
        print("起了 pid", p.pid, "· 每天 %02d:%02d 写当天日结" % AT)
    elif cmd == "stop":
        if os.path.exists(PIDF):
            try:
                pid = int(open(PIDF).read().strip()); os.kill(pid, 15)
                os.remove(PIDF); print("停了", pid)
            except Exception as e:
                print("停不了:", e)
        else:
            print("没在跑")
    elif cmd == "_loop":
        loop()
    else:
        if os.path.exists(PIDF):
            pid = int(open(PIDF).read().strip())
            print("在跑 pid %s %s" % (pid, "（活着）" if alive(pid) else "（僵尸，需重启）"))
        else:
            print("没在跑")
        if os.path.exists(LOG):
            print("最近日志：")
            print("".join(open(LOG).readlines()[-5:]))

if __name__ == "__main__":
    main()
