#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""域重排：问「我……」的时候，先在他自己的堆里找，别让元记忆抢位。

今天下午试出来的毛病：问「我熬夜多吗」，前 3 名全是 entity=记忆系统/记忆插件
的元记忆（因为「熬夜」这个词也出现在讨论同义词组的那几条里），真答案排第 7。

做法（他教的拼图第一步：先认主体，定堆）：
  问句里有「我 / 我的」、又没有系统词（记忆/检索/插件/代码/评测…）
  → 把生活/关系/健康/个人成长/情绪/社交这几个域的条目提到前面，其余不动。
只改顺序，不加候选。
"""
import os
import sys
import json
import importlib.util

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MINE_TAG = re.compile if False else None
DOMAIN = {"生活", "关系", "健康", "个人成长", "情绪与自我", "社交"}
SYSWORDS = ("记忆系统", "检索", "插件", "代码", "评测", "跑分", "索引", "脚本",
            "向量", "标签", "数据库", "部署", "版本")

import re


def about_him(q):
    if not re.search(r"我", q):
        return False
    return not any(w in q for w in SYSWORDS)


def main():
    args = sys.argv[1:]
    top = 10
    if "--top" in args:
        i = args.index("--top"); top = int(args[i + 1]); del args[i:i + 2]
    q = " ".join(args).strip()
    if not q:
        print(__doc__); return

    spec = importlib.util.spec_from_file_location("memcore", os.path.join(BASE, "memory.py"))
    mem = importlib.util.module_from_spec(spec); spec.loader.exec_module(mem)
    res = mem.search_curated({"query": q, "limit": 30})
    items = res[0] if isinstance(res, tuple) else (res.get("items") or [])

    flag = about_him(q)
    if flag:
        head = [x for x in items if (x.get("big_tag") in DOMAIN)]
        tail = [x for x in items if (x.get("big_tag") not in DOMAIN)]
        re_ = head + tail
    else:
        re_ = items

    def show(tag, lst):
        print("【%s】" % tag)
        for it in lst[:top]:
            ts = str(it.get("ts") or it.get("timestamp") or "")[:16]
            print("  [%s] %s/%s | %s" % (ts, it.get("big_tag") or "-", it.get("entity") or "-",
                                         (it.get("text") or "")[:40].replace("\n", " ")))

    print("问题：%s　问他自己？%s" % (q, "是" if flag else "否"))
    show("原顺", items)
    if flag:
        show("域重排（生活/关系/健康…提到前面）", re_)


if __name__ == "__main__":
    main()
