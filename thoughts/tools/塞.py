#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""随机把「去看看念头」塞进 AGENTS.md。

不按轮，按运气：每隔一段时间掷一次骰子，中了就把那一段写进去，
没中就把它拿走。文件一变，指令就会重新进到当前会话里 —— 所以
「出现」和「消失」本身就是那次随机。

    python3 塞.py tick [p]            掷一次（p 默认 0.35）
    python3 塞.py on|off              直接摆上 / 拿走
    python3 塞.py loop [--every 600] [--p 0.35]
    python3 塞.py status

规矩：只碰 <!-- 念头:start --> 和 <!-- 念头:end --> 之间，别处一个字不动。
"""
import os
import random
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

F = os.path.expanduser("~/.dsh/AGENTS.md")
S = "<!-- 念头:start -->"
E = "<!-- 念头:end -->"

BLOCK = """## 8. 念头（每轮掷一次）

每一轮，我自己掷一次：大约 **10%** 的轮次会去看一眼念头。

（真去了就跑：`python3 "thoughts（念头）/tools/偏斜.py" last`）

- 掷出「不去」就是不去 —— 不用给理由，不用交代，也不写进任何交付物。
- 掷没掷、掷出几，没有记录，也不用向谁证明。
- 去了别写「受 #x 影响」这种交代 —— 那是包装，不是念头。
"""


def read():
    with open(F, encoding="utf-8") as f:
        return f.read()


def split(text):
    i = text.find(S)
    j = text.find(E)
    if i < 0 or j < 0:
        return text, None, None
    return text[:i], text[i + len(S):j], text[j + len(E):]


def render(on):
    return S + "\n" + (BLOCK if on else "") + "\n" + E


def set_state(on):
    text = read()
    head, mid, tail = split(text)
    if mid is None:
        raise SystemExit("AGENTS.md 里没找到念头标记，先手动加一遍")
    cur = "去看看念头" in (mid or "")
    new = head + render(on) + tail
    if cur == on:
        return False                      # 没变化就不写，免得白白触发一次刷新
    if new == text:
        return False
    with open(F, "w", encoding="utf-8") as f:
        f.write(new)
    return True


def status():
    _, mid, _ = split(read())
    on = "去看看念头" in (mid or "")
    print("现在：", "摆着" if on else "没摆")


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd == "on":
        print("改了" if set_state(True) else "本来就在")
    elif cmd == "off":
        print("拿走了" if set_state(False) else "本来就没有")
    elif cmd == "status":
        status()
    elif cmd == "tick":
        p = float(a[1]) if len(a) > 1 else 0.35
        on = random.random() < p
        changed = set_state(on)
        print("中" if on else "没中", "· 文件" + ("变了" if changed else "没动"))
    elif cmd == "loop":
        every, p = 600, 0.35
        i = 1
        while i < len(a):
            if a[i] == "--every":
                every = int(a[i + 1]); i += 2
            elif a[i] == "--p":
                p = float(a[i + 1]); i += 2
            else:
                i += 1
        print(f"loop：每 {every}s 掷一次，p={p}", flush=True)
        while True:
            on = random.random() < p
            changed = set_state(on)
            print(time.strftime("%H:%M:%S"), "中" if on else "没中", "变了" if changed else "", flush=True)
            time.sleep(every)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
