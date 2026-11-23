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
ROOT = os.environ.get("DSH_ROOT", "/sdcard/dsh")
SELF_TABLE = os.path.join(ROOT, "memory（记忆库）/词表/自创表.json")
# 仓库里带一份占位词表（与真表同结构、同条数，内容与「我」无关）。
# 找不到自己的词表时用它，好让整条链能跑通；真词在 DSH_ROOT 里那份。
EXAMPLE_TABLE = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                             "memory", "example-自创表.json")
SEED = "表v2|20261123"


def _resolve_self_table(explicit=None):
    if explicit:
        return explicit
    if os.path.exists(SELF_TABLE):
        return SELF_TABLE
    if os.path.exists(EXAMPLE_TABLE):
        sys.stderr.write(
            "！没找到你的词表 %s\n"
            "  先用仓库里的占位词表跑通：%s\n"
            "  想用自己的，写一份同结构的 JSON，再 --self <路径>\n"
            % (SELF_TABLE, EXAMPLE_TABLE))
        return EXAMPLE_TABLE
    sys.exit("词表找不到：%s（仓库里也没有占位的 example-自创表.json）" % SELF_TABLE)


def load_words(explicit=None):
    p = _resolve_self_table(explicit)
    d = db.read_json(p)
    return [k for k in d if not k.startswith("_")]


def cmd_build(args):
    self_path = None
    for i, a in enumerate(args):
        if a == "--self":
            self_path = args[i + 1]
    t = db.read_json(F_TABLE)
    if "words" in t:
        sys.exit("表已经挂过词了，不改——改表就是新的一代，不是修。"
                 "真要重来，删掉 origin/表.json 里的 words 再 build。")
    words = load_words(self_path)
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
    t["words_note"] = ("%d 条词挂在 %d 个符号上，%d 个符号留空（读作 □）。"
                       "定死。改表＝新的一代，不是修。"
                       % (len(words), len(words), len(listed) - len(words)))
    db.write_json(F_TABLE, t, backup=True)
    print("挂好了：%d 条词，%d 个空位，seed=%s" % (
        sum(1 for v in listed if v), sum(1 for v in listed if not v), SEED))
    print("words sha %s" % t["words_sha256"][:16])


def cmd_show(args):
    want = None
    for i, a in enumerate(args):
        if a == "--self":
            continue
        want = a
    if not want:
        sys.exit("用法：解读.py show <编号> [--self <词表.json>]")
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
