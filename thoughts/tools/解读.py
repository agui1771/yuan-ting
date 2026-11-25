#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解读 —— 第 1 层。把符号串换成词，别的什么都不干。

    python3 解读.py build         把自创表的 81 条挂到 81 个符号上（一次性，定死）
    python3 解读.py show <编号>    打印这一条解出来的词

表只管词，不管关系。解出来是一堆并排的词：没标点、没语序、没连词。
它们之间是什么关系，表不给——只能读的人给。

空位（没有挂词的符号）打成 □。读到□，那一段就是没词可翻。
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("dianbo", os.path.join(HERE, "电波.py"))
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

F_TABLE = db.F_TABLE
SELF_TABLE = "/sdcard/dsh/memory（记忆库）/词表/自创表.json"
SEED = "表v2|20261123"


def load_words():
    d = db.read_json(SELF_TABLE)
    return [k for k in d if not k.startswith("_")]


def cmd_build(args):
    t = db.read_json(F_TABLE)
    if "words" in t:
        sys.exit("表已经挂过词了，不改——改表就是新的一代，不是修。")
    words = load_words()
    order = db.Stream(SEED).shuffled(db.SYMBOLS)
    if len(words) > len(order):
        sys.exit("词比符号多")
    words_of = {}
    for sym in db.SYMBOLS:
        words_of[sym] = None
    for i, sym in enumerate(order):
        words_of[sym] = words[i] if i < len(words) else None
    listed = [words_of[s] for s in db.SYMBOLS]
    t["words"] = words_of
    t["words_sha256"] = db.sha(listed)
    t["words_note"] = ("81 条自创词挂在 81 个符号上，19 个符号留空。"
                       "定死。改表＝新的一代，不是修。")
    db.write_json(F_TABLE, t, backup=True)
    print("挂好了：%d 条词，%d 个空位，seed=%s" % (
        sum(1 for v in listed if v), sum(1 for v in listed if not v), SEED))
    print("words sha %s" % t["words_sha256"][:16])


def cmd_show(args):
    if not args:
        sys.exit("用法：解读.py show <编号>")
    want = args[0]
    t = db.read_json(F_TABLE)
    words_of = t["words"]
    for fn in sorted(os.listdir(db.ORIGIN)):
        if not (fn.endswith(".json") and fn[:8].isdigit()):
            continue
        for it in db.read_json(os.path.join(db.ORIGIN, fn))["items"]:
            if it["id"] == want:
                out = [words_of.get(ch) or "□" for ch in it["s"]]
                print(" ".join(out))
                holes = sum(1 for o in out if o == "□")
                sys.stderr.write("%s  %d 个词  空位 %d\n" % (want, len(out) - holes, holes))
                return
    sys.exit("没有这条：%s" % want)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "build":
        return cmd_build(argv[2:])
    if argv[1] == "show":
        return cmd_show(argv[2:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
