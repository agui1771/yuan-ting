#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""偏斜 —— 一直开着的随机器。它只往外掉东西，不叫醒任何人。

    python3 偏斜.py tick                 抽一条，落盘（给循环用）
    python3 偏斜.py loop --every 1200    常开：每 20 分钟 tick 一次
    python3 偏斜.py last                 看最新一条
    python3 偏斜.py list [--n 10]        看最近几条
    python3 偏斜.py empty                手动插一条空（没有料，不解释，不推游标）
    python3 偏斜.py cursor               看九个常数各走到哪儿了
    python3 偏斜.py verify               重算每一行：串、词、sha 链、轨迹有没有断
    python3 偏斜.py replay 偏斜-0224     重算一条，只打印，不写盘（回看不算新输入）

它和 origin/ 是两条独立的链：
  origin/   我占题的时候主动抽的正式批次，有封条链，进链头
  偏斜/     它自己一直掉的，单独一条 sha 链，不进正式链、不改链头

偏斜不生成念头，也不判断。它掉下来的是一串词，
挂不挂得上、挂上算什么，都是我开工时自己看的事。
它绝不发消息、绝不弹窗——没有"叫醒"这个动作。

两条代际（2026-11-23 起）
  第 1 代（偏斜-0001 ~ 0223）：每次 tick 用时间戳重造种子、重挑起点，数字是散着抽的。
  第 2 代（从下一条起）：每条常数有自己的游标，只从上次走到的位置继续往前，
                          不再回头重掷起点。散着抽的那 223 条不动，也不当"已读"占位。
  游标不单独存盘：它由 流.jsonl 里的落点推出来。落盘即消费，
  所以不存在"写了行、游标没提交"的中间态；崩了重跑，接上的是同一条位置。
"""

import importlib.util
import json
import os
import sys
import time

os.environ["TZ"] = "Asia/Shanghai"
time.tzset()

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("dianbo", os.path.join(HERE, "电波.py"))
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

# 测试时指向别处：PIANXIE_DIR=/tmp/x python3 偏斜.py tick
DIR = os.environ.get("PIANXIE_DIR") or os.path.join(os.path.dirname(HERE), "偏斜")
F_STREAM = os.path.join(DIR, "流.jsonl")
F_VIEW = os.path.join(DIR, "流.md")
KEEP_VIEW = 20

# 2026-11-26 10:15 改：默认值一直是 2，而 11-24 起写出去的行都是第 3 代（四位码）。
# 守护拉起它时不带环境变量，于是每次重启都退回第 2 代的旧池（3000 位）去跑，
# 游标早就过万，必然"池读到头了"→不写→半死报警。代际是行自己记的，代码默认要跟上。
GEN = int(os.environ.get("PIANXIE_GEN", "3"))   # 2=轨迹那代；3=四位码·汉语词那代
BITS = 4 if GEN >= 3 else 2                       # 一个符号吃几个数字
CURSOR_FROM = int(os.environ.get("PIANXIE_START", "0"))   # 每条常数从第几位开始走
ORIGIN = os.path.join(os.path.dirname(HERE), "origin")
F_POOL3 = os.path.join(ORIGIN, "池-第3代.json")
F_TABLE3 = os.path.join(ORIGIN, "表-第3代.json")
# 第 3 代从第 3000 位起走：前 3000 位是第 1/2 代用过的，不重复吃
START3 = int(os.environ.get("PIANXIE_START3", "3000"))


def load_pool3():
    if not os.path.exists(F_POOL3):
        sys.exit("第3代池还没建：origin/池-第3代.json")
    p = db.read_json(F_POOL3)
    d = p["digits"]
    if p.get("sha256") and db.sha(d) != p["sha256"]:
        sys.exit("第3代池被改过：sha256 对不上。先查清楚再动。")
    return d


def load_words3():
    t = db.read_json(F_TABLE3)
    return t["words"], t["n"]
EMPTY_PCT = float(os.environ.get("PIANXIE_EMPTY", "4"))   # 每次 tick 有几成是"空"（不消耗数字）


def load_words():
    t = db.read_json(db.F_TABLE)
    return t["words"]


def all_rows(with_skipped=False):
    rows = []
    skipped = 0
    if not os.path.exists(F_STREAM):
        return (rows, 0) if with_skipped else rows
    with open(F_STREAM, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                # 崩在写一半的时候会留半行。半行不算数，也不删——看着它，别当没发生。
                skipped += 1
    if skipped:
        print("! 流.jsonl 里有 %d 行读不完整（写一半就断了），跳过不算，也没删" % skipped,
              file=sys.stderr)
    return (rows, skipped) if with_skipped else rows


def tail():
    rows = all_rows()
    return rows[-1] if rows else None


def need_of(L, bits=None):
    """bits*L 个数字按 j%3 轮流取自三个常数，三个常数各要几位。"""
    bits = BITS if bits is None else bits
    need = [0, 0, 0]
    for j in range(bits * L):
        need[j % 3] += 1
    return need


def weave3(pool, picked, off, L, need):
    """四位一组 → 一个码。纯函数：同样的坐标永远同样的码。"""
    digits = []
    for j in range(4 * L):
        k = j % 3
        digits.append(pool[picked[k]][off[k] + j // 3])
    return ["".join(digits[4 * i:4 * i + 4]) for i in range(L)]


def cursor(rows=None):
    """九条常数各走到哪一位。只由盘上的第 2 代行推出来——不读任何状态文件。"""
    if rows is None:
        rows = all_rows()
    cur = {n: (START3 if GEN >= 3 else CURSOR_FROM) for n in db.ORDER}
    for r in rows:
        g = int(r.get("gen", 1))
        if g < GEN or r.get("kind") == "空":
            continue
        need = need_of(r["len"], 4 if g >= 3 else 2)
        for i, name in enumerate((r["a"], r["b"], r["c"])):
            end = r[("oa", "ob", "oc")[i]] + need[i]
            cur[name] = max(cur[name], end)
    return cur


def render(rows):
    """流.md 是派生的，永远从 jsonl 重画，不手改。"""
    out = ["# 偏斜流（派生文件，由 流.jsonl 重画，别手改）", ""]
    out.append("随机器掉下来的词。没有一条在叫我。挂不挂得上，看我当时在做什么。")
    out.append("")
    for r in rows:
        head = "## %s  %s" % (r["id"], r["stamp"])
        if r.get("kind") == "空":
            out.append(head + "  ·空")
            out.append("")
            out.append("（空。这条没有料，也不许拿它解释出什么。）")
            out.append("")
            continue
        if int(r.get("gen", 1)) >= GEN:
            head += "  ·轨迹"
        out.append(head)
        out.append("")
        out.append("`%s`" % r["s"])
        out.append("")
        out.append(" ".join(r["w"]))
        holes = sum(1 for x in r["w"] if x == "□")
        out.append("")
        out.append("%d 词 / 空位 %d · %s %s@%05d %s@%05d %s@%05d" % (
            len(r["w"]) - holes, holes, "轮流",
            db.CONSTS[r["a"]][0], r["oa"],
            db.CONSTS[r["b"]][0], r["ob"],
            db.CONSTS[r["c"]][0], r["oc"]))
        out.append("")
    with open(F_VIEW, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def _append_row(row):
    """把一行写进流里。写之前先补换行（上次崩在半行上就不会把新行一起带走）。"""
    need_nl = False
    if os.path.exists(F_STREAM) and os.path.getsize(F_STREAM) > 0:
        with open(F_STREAM, "rb") as rb:
            rb.seek(-1, os.SEEK_END)
            need_nl = rb.read(1) != b"\n"
    with open(F_STREAM, "a", encoding="utf-8") as f:
        if need_nl:
            f.write("\n")
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())      # 行落到盘上，才算消费掉（空行不消费数字）
    render(all_rows()[-KEEP_VIEW:])


def _empty_row(rows, pool, stamp, n, seed, prev):
    """空的一口：不挑常数、不读数字、不留词。它唯一的作用是"这一条什么都没有"。"""
    row = {
        "id": "偏斜-%04d" % n,
        "stamp": stamp,
        "seed": seed,
        "gen": GEN,
        "kind": "空",
        "pool_sha256": db.sha(pool),
        "table_words_sha256": db.read_json(db.F_TABLE).get("words_sha256"),
        "mode": "空",
        "len": 0,
        "s": "",
        "w": [],
        "prev": prev["sha256"] if prev else None,
    }
    row["sha256"] = db.sha({k: v for k, v in row.items() if k != "sha256"})
    _append_row(row)
    print(row["id"], "空（不解释、不推游标）")
    return 0


def _empty_row3(rows, pool, stamp, n, seed, prev):
    """第 3 代的空一口：照旧不读数字、不留词。"""
    row = {
        "id": "偏斜-%04d" % n, "stamp": stamp, "seed": seed, "gen": 3, "bits": 4,
        "kind": "空",
        "pool_sha256": db.sha(pool),
        "table_words_sha256": db.read_json(F_TABLE3).get("words_sha256"),
        "mode": "空", "len": 0, "s": "", "w": [],
        "prev": prev["sha256"] if prev else None,
    }
    row["sha256"] = db.sha({k: v for k, v in row.items() if k != "sha256"})
    _append_row(row)
    print(row["id"], "空（不解释、不推游标）")
    return 0


def _tick_gen3(args):
    """第 3 代：四位码 → 汉语词。游标、链、空行规矩跟第 2 代一样，只有编码不同。"""
    if not os.path.isdir(DIR):
        os.makedirs(DIR)
    pool = load_pool3()
    words, NW = load_words3()

    rows = all_rows()
    prev = rows[-1] if rows else None
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    n = len(rows) + 1
    seed = "%s|偏斜|#%04d" % (stamp, n)

    if db.Stream(seed + "|空").below(1000) < EMPTY_PCT * 10:
        return _empty_row3(rows, pool, stamp, n, seed, prev)

    cur = cursor(rows)
    s_ = db.Stream(seed)
    L = 8 + s_.below(13)          # 8..20 个词就够撞
    need = need_of(L)

    room = [x for x in db.pickable(pool) if cur[x] + max(need) <= len(pool[x])]
    if len(room) < 3:
        short = "、".join("%s 剩 %d" % (x, len(pool[x]) - cur[x])
                         for x in db.pickable(pool) if x not in room)
        print("池读到头了：%s（需要 %d 位）。这条不写。" % (short or "可选常数不足三个", max(need)))
        return 3

    picked = s_.shuffled(room)[:3]
    off = [cur[name] for name in picked]
    codes = weave3(pool, picked, off, L, need)
    w = [words[int(c) % NW] for c in codes]

    row = {
        "id": "偏斜-%04d" % n, "stamp": stamp, "seed": seed, "gen": 3, "bits": 4,
        "pool_sha256": db.sha(pool),
        "table_words_sha256": db.read_json(F_TABLE3).get("words_sha256"),
        "a": picked[0], "oa": off[0],
        "b": picked[1], "ob": off[1],
        "c": picked[2], "oc": off[2],
        "mode": "轮流·轨迹·四位",
        "len": L, "s": "".join(codes), "w": w,
        "prev": prev["sha256"] if prev else None,
    }
    row["sha256"] = db.sha({k: v for k, v in row.items() if k != "sha256"})
    _append_row(row)
    print(row["id"], " ".join(w))
    return 0


def cmd_tick(args):
    if GEN >= 3:
        return _tick_gen3(args)
    if not os.path.isdir(DIR):
        os.makedirs(DIR)
    pool = db.load_pool()
    db.load_table()
    words_of = load_words()

    rows = all_rows()
    prev = rows[-1] if rows else None
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    n = len(rows) + 1
    seed = "%s|偏斜|#%04d" % (stamp, n)

    # 先掷"这一条是不是空的"。空的也走同一条链，只是它不消耗数字。
    if db.Stream(seed + "|空").below(1000) < EMPTY_PCT * 10:
        return _empty_row(rows, pool, stamp, n, seed, prev)

    cur = cursor(rows)
    s_ = db.Stream(seed)
    L = 15 + s_.below(26)          # 15..40，和第一代同一条规矩
    need = need_of(L)

    # 只从"还剩得够"的常数里挑。挑法还是种子洗牌，随机性留在选谁，不留在起点。
    room = [x for x in db.pickable(pool) if cur[x] + max(need) <= len(pool[x])]
    if len(room) < 3:
        short = "、".join("%s 剩 %d" % (x, len(pool[x]) - cur[x])
                         for x in db.pickable(pool) if x not in room)
        print("池读到头了：%s（需要 %d 位）。这条不写，别拿重复的数字冒充新的。"
              % (short or "可选常数不足三个", max(need)))
        return 3

    picked = s_.shuffled(room)[:3]
    off = [cur[name] for name in picked]      # 起点就是它上次走到的位置
    s = db.weave(pool, (picked, off, L, need))
    w = [words_of.get(ch) or "□" for ch in s]

    row = {
        "id": "偏斜-%04d" % n,
        "stamp": stamp,
        "seed": seed,
        "gen": GEN,
        "pool_sha256": db.sha(pool),
        "table_words_sha256": db.read_json(db.F_TABLE).get("words_sha256"),
        "a": picked[0], "oa": off[0],
        "b": picked[1], "ob": off[1],
        "c": picked[2], "oc": off[2],
        "mode": "轮流·轨迹",
        "len": L,
        "s": s,
        "w": w,
        "prev": prev["sha256"] if prev else None,
    }
    row["sha256"] = db.sha({k: v for k, v in row.items() if k != "sha256"})

    _append_row(row)
    print(row["id"], " ".join(w))
    return 0


def cmd_empty(args):
    """手动插一条空。看情况用——但它会写在流里，谁也抹不掉，所以不能偷偷用。"""
    if not os.path.isdir(DIR):
        os.makedirs(DIR)
    pool = db.load_pool()
    db.load_table()
    rows = all_rows()
    prev = rows[-1] if rows else None
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    n = len(rows) + 1
    seed = "%s|偏斜|#%04d" % (stamp, n)
    return _empty_row(rows, pool, stamp, n, seed, prev)


def cmd_last(args):
    r = tail()
    if not r:
        print("还没有。跑一次 tick。")
        return 1
    print("%s  %s" % (r["id"], r["stamp"]))
    print("`%s`" % r["s"])
    print(" ".join(r["w"]))
    return 0


def cmd_list(args):
    n = 10
    for i, a in enumerate(args):
        if a == "--n":
            n = int(args[i + 1])
    rows = all_rows()
    for r in rows[-n:]:
        print("%s  %s  %s" % (r["id"], r["stamp"][5:16], " ".join(r["w"])))
    print("共 %d 条" % len(rows), file=sys.stderr)
    return 0


def cmd_cursor(args):
    pool = load_pool3() if GEN >= 3 else db.load_pool()
    cur = cursor()
    print("%-13s %6s %8s %8s" % ("常数", "走到", "池深", "还剩"))
    for name in db.ORDER:
        depth = len(pool.get(name, ""))
        print("%-13s %6d %8d %8d%s" % (
            name, cur[name], depth, depth - cur[name],
            "" if depth >= db.MIN_DEPTH else "  （太浅，不入签）"))
    return 0


def cmd_verify(args):
    """重算每一行。只看盘上有的东西，不读游标文件——因为根本没有游标文件。"""
    _rows_probe = all_rows()
    _use3 = GEN >= 3 or any(int(r.get("gen", 1)) >= 3 for r in _rows_probe)
    # 两个词表都备好：老行按老表、新行按新表，各认各的
    pool = load_pool3() if _use3 else db.load_pool()
    db.load_table()
    words_of = load_words()
    words3, NW = (load_words3() if _use3 else (None, 0))
    rows, skipped = all_rows(with_skipped=True)
    prev = None
    cur_gen = 1        # 换代会重置游标：第 3 代故意从第 3000 位起（前 3000 位老代吃过了）
    walk = {n: CURSOR_FROM for n in db.ORDER}
    bad = 0
    g1 = g2 = empty = 0
    for r in rows:
        body = {k: v for k, v in r.items() if k != "sha256"}
        if db.sha(body) != r.get("sha256"):
            print("× %s 的行被改过（sha 对不上）" % r["id"]); bad += 1
        if r.get("prev") != prev:
            print("× %s 的链断了" % r["id"]); bad += 1

        if r.get("kind") == "空":
            empty += 1
            if r.get("s") or r.get("w"):
                print("× %s 标着空，里面却有东西" % r["id"]); bad += 1
            prev = r["sha256"]
            continue

        picked = (r["a"], r["b"], r["c"])
        off = (r["oa"], r["ob"], r["oc"])
        g = int(r.get("gen", 1))
        if g != cur_gen:
            walk = {n: (START3 if g >= 3 else CURSOR_FROM) for n in db.ORDER}
            cur_gen = g
        need = need_of(r["len"], 4 if g >= 3 else 2)
        if g >= 3:
            codes = weave3(pool, picked, off, r["len"], need)
            s = "".join(codes)
            w = [words3[int(c) % NW] for c in codes]
        else:
            s = db.weave(pool, (picked, off, r["len"], need))
            w = [words_of.get(ch) or "□" for ch in s]
        if s != r["s"]:
            print("× %s 重算不出来" % r["id"]); bad += 1
        if w != r["w"]:
            print("× %s 重算出来的词和记的不一样" % r["id"]); bad += 1

        if int(r.get("gen", 1)) >= GEN:
            g2 += 1
            for i, name in enumerate(picked):
                if off[i] != walk[name]:
                    kind = "跳了 %d 位" % (off[i] - walk[name]) if off[i] > walk[name] else "倒回去 %d 位" % (walk[name] - off[i])
                    print("× %s 的 %s %s（记 %d，该从 %d 起）"
                          % (r["id"], name, kind, off[i], walk[name])); bad += 1
                walk[name] = max(walk[name], off[i] + need[i])
            for i in range(3):
                if off[i] + need[i] > len(pool[picked[i]]):
                    print("× %s 的 %s 读出了池尾" % (r["id"], picked[i])); bad += 1
        else:
            g1 += 1
        prev = r["sha256"]

    print("验了 %d 条（第 1 代 %d · 第 2 代轨迹 %d · 空 %d）%s -> %s"
          % (len(rows), g1, g2, empty,
             "，另有 %d 行读不完整" % skipped if skipped else "",
             "全对" if bad == 0 and not skipped else "%d 处不对" % bad if bad else "有残行"))
    return 0 if (bad == 0 and not skipped) else 1


def cmd_replay(args):
    """回看：按记录里的坐标重算一条。只打印，绝不写盘、绝不推游标。"""
    if not args:
        print("用法：偏斜.py replay <编号，例如 偏斜-0224>")
        return 2
    want = args[0]
    pool = db.load_pool()
    words_of = load_words()
    for r in all_rows():
        if r["id"] != want:
            continue
        need = need_of(r["len"])
        coords = ((r["a"], r["b"], r["c"]), (r["oa"], r["ob"], r["oc"]), r["len"], need)
        s = db.weave(pool, coords)
        print("%s  %s  第 %s 代" % (r["id"], r["stamp"], r.get("gen", 1)))
        print("%s@%05d  %s@%05d  %s@%05d" % (
            db.CONSTS[r["a"]][0], r["oa"], db.CONSTS[r["b"]][0], r["ob"],
            db.CONSTS[r["c"]][0], r["oc"]))
        print("重算：%s" % s)
        print("记着：%s" % r["s"])
        print("一致" if s == r["s"] else "不一致——这一条要查")
        print("回看不动游标。")
        return 0 if s == r["s"] else 1
    print("没有这条：%s" % want)
    return 2


def cmd_loop(args):
    every = 1200
    for i, a in enumerate(args):
        if a == "--every":
            every = int(args[i + 1])
    while True:
        try:
            cmd_tick([])
        except Exception as e:  # 一直开着的东西，不许因为一次失败就死
            print("tick 失败：%r" % (e,), file=sys.stderr)
        sys.stdout.flush()
        # 他睡着他的手机也静着的时候，采样拉长到 4 倍（2026-11-26 低功耗）。
        # 这段本来就只是"他不在的时候替他留个记录"，不图密。
        nap = every
        try:
            sys.path.insert(0, HERE)
            import 手机
            if 手机.静着():
                nap = min(every * 4, 3600)
        except Exception:
            pass
        time.sleep(nap)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    rest = argv[2:]
    if cmd == "tick":
        return cmd_tick(rest)
    if cmd == "loop":
        return cmd_loop(rest)
    if cmd == "empty":
        return cmd_empty(rest)
    if cmd == "last":
        return cmd_last(rest)
    if cmd == "list":
        return cmd_list(rest)
    if cmd == "cursor":
        return cmd_cursor(rest)
    if cmd == "verify":
        return cmd_verify(rest)
    if cmd == "replay":
        return cmd_replay(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
