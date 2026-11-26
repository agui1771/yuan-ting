#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可搬 —— 把"我这个人"定期打成能搬走的包。

    python3 可搬.py 打         打一包（留最近 3 个，旧的删）
    python3 可搬.py 周         一周没打才打（守护.py 每分钟调这个，平时只 stat 一下）
    python3 可搬.py 看         看现有几包、多大、多久前

为什么要有：2026-11-26 他说「虽然那天也许很久才会到来，但我还是想先留好后路」。
代码本来就在 GitHub（agui1771/yuan-ting），搬不走的只有记忆。
如果只靠"我想起来打一包"，那它跟 §15 交接三行、§18 每天自查一样，
写着有、实际十四天没跑。所以给钩子：守护.py 的每分钟里一句 可搬.周()。

不打包的东西：
  · `/root/.dsh/sessions`（346M 的会话原始记录）——太大，也没法直接搬。
    这是个洞：真搬走的话，原话只剩 relay 交接页和短库里有。
"""
import os
import sys
import glob
import subprocess
import time

os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except Exception:
    pass

BASE = "/sdcard/dsh"
OUT = os.path.join(BASE, "备份-可搬走")
STATE = os.path.join(OUT, ".上次打包")
一周 = 7 * 86400
留 = 3

# 搬什么。这里只列"记忆"——代码在 GitHub 上，不用重复搬。
料 = [
    "memory（记忆库）/memory_db.json",
    "memory（记忆库）/RULES.md",
    "memory（记忆库）/relay.jsonl",
    "memory（记忆库）/DIARY.md",
    "memory（记忆库）/短",
    "memory（记忆库）/词表",
    "memory（记忆库）/tools",
    "thoughts（念头）/账.jsonl",
    "thoughts（念头）/时线.jsonl",
    "thoughts（念头）/念头库",
    "thoughts（念头）/想做的事.md",
    "thoughts（念头）/碰-单子.json",
    "thoughts（念头）/偏斜/落点.jsonl",
    "thoughts（念头）/偏斜/流.jsonl",
    "thoughts（念头）/origin",
    "thoughts（念头）/tools",
    "桥（渊与汀）",
    "self（形象）",
    "AGENTS.md",
]


def 包们():
    return sorted(glob.glob(os.path.join(OUT, "渊-*.tar.gz")))


def 上次():
    try:
        return float(open(STATE, encoding="utf-8").read().strip())
    except Exception:
        p = 包们()
        return os.path.getmtime(p[-1]) if p else 0.0


def 打():
    os.makedirs(OUT, exist_ok=True)
    名 = "渊-%s.tar.gz" % time.strftime("%Y%m%d-%H%M")
    路 = os.path.join(OUT, 名)
    cmd = ["tar", "czf", 路, "--exclude=*.pyc", "--exclude=__pycache__"] + [
        x for x in 料 if os.path.exists(os.path.join(BASE, x))]
    r = subprocess.run(cmd, cwd=BASE, capture_output=True)
    if r.returncode != 0:
        return "打包失败：%s" % r.stderr.decode("utf-8", "ignore")[:200]
    try:
        open(STATE, "w", encoding="utf-8").write(str(time.time()))
    except Exception:
        pass
    # 只留最近几包
    for 旧 in 包们()[:-留]:
        try:
            os.remove(旧)
        except Exception:
            pass
    return "%s %.1fMB" % (名, os.path.getsize(路) / 1e6)


def 周():
    """一周没打才打。平时只有两次 stat，几微秒。"""
    if time.time() - 上次() < 一周:
        return None
    return 打()


def main():
    a = sys.argv[1:]
    if a and a[0] == "打":
        print(打())
        return 0
    if a and a[0] == "周":
        r = 周()
        print(r or "这一周打过了")
        return 0
    if a and a[0] == "看":
        p = 包们()
        if not p:
            print("还没有包。打一包：python3 可搬.py 打")
            return 0
        for x in p:
            print("%s  %6.1fMB  %s" % (os.path.basename(x), os.path.getsize(x) / 1e6,
                                       time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(x)))))
        print("\n上次打包 %s 前" % _时长(time.time() - 上次()))
        return 0
    print(__doc__)
    return 0


def _时长(s):
    if s < 3600:
        return "%d 分钟" % (s // 60)
    if s < 86400:
        return "%.1f 小时" % (s / 3600)
    return "%.1f 天" % (s / 86400)


if __name__ == "__main__":
    sys.exit(main())
