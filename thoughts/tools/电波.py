#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电波 —— 第 0 层。只冒，不解释。

    python3 电波.py build [--digits N]   建/扩数字池（一次性，需要 mpmath）
    python3 电波.py bang  [--n 20]       冒一批
    python3 电波.py verify               全链校验（池 / 表 / 每条 / 链）
    python3 电波.py show <编号>           单条原样打印

这个程序不做的四件事：不当真、不解释、不翻译、不改。
规矩见 ../电波-20261123.md。

目录：
    tools/电波.py      <— 你在这儿
    origin/表.json     100 个符号（定死）
    origin/池.json     九个常数的十进制数字（只增）
    origin/YYYYMMDD-NNN.json   一批原产物
    origin/链头.json   缓存，丢了能从批次文件重建
"""

import hashlib
import json
import os
import sys
import time
from fractions import Fraction

# 容器默认是 UTC，但这套东西按上海时间记。固定住，免得批号差一天。
os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except AttributeError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORIGIN = os.path.join(ROOT, "origin")
F_POOL = os.path.join(ORIGIN, "池.json")
F_TABLE = os.path.join(ORIGIN, "表.json")
F_HEAD = os.path.join(ORIGIN, "链头.json")

# ── 表：100 个符号 ────────────────────────────────────────────────
# 刻意不用任何文字的字母，也不用一眼能读的形状。
SYMBOLS = list(
    "⌶⌷⌸⌹⌺⌻⌼⌽⌾⌿"
    "⍀⍁⍂⍃⍄⍅⍆⍇⍈⍉"
    "⍊⍋⍌⍍⍎⍏⍐⍑⍒⍓"
    "⍔⍕⍖⍗⍘⍙⍚⍛⍜⍝"
    "⍞⍟⍠⍡⍢⍣⍤⍥⍦⍧"
    "⍨⍩⍪⍫⍬⍭⍮⍯⍰⍱"
    "⍲⍳⍴⍵⍶⍷⍸⍹⍺"
    "⌁⌂⌃⌄⌅⌆⌇⌌⌍⌎"
    "⌏⌐⌑⌒⌓⌔⌕⌖⌗⌘"
    "⌙⌜⌝⌞⌟⌠⌡⌢⌣⌤⌥"
)
assert len(SYMBOLS) == 100 and len(set(SYMBOLS)) == 100

# ── 九个常数 ──────────────────────────────────────────────────────
# name -> (显示名, 位, 公开已知前缀，用来在 build 时自检)
CONSTS = {
    "pi":          ("π",                "形",   "141592653589793238462643383279502884197169399375105820974944"),
    "phi":         ("φ",                "度",   "618033988749894848204586834365638117720309179805762862135448"),
    "e":           ("e",                "生",   "718281828459045235360287471352662497757247093699959574966967"),
    "gamma":       ("γ",                "隙",   "577215664901532860606512090082402431042159335939923598805767"),
    "zeta3":       ("ζ(3)",             "三",   "202056903159594285399738161511449990764986292340498881792271"),
    "delta":       ("δ",                "混沌", "669201609102990671853203820466201617258185577475768632745651"),
    "g1":          ("γ₁",               "临界", "134725141734693790457251983562470270784257115699243175685567"),
    "mu":          ("μ",                "生数", "451369234883381050283968485892027449493032283648015863093004"),
    "champernowne":("Champernowne",     "万物", "123456789101112131415161718192021222324252627282930313233343"),
}
ORDER = list(CONSTS)


# ── 小工具 ───────────────────────────────────────────────────────
def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def sha_str(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def file_sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(p, obj, backup=False):
    """写文件。如果目标已存在且 backup=True，先留一份 .bak。"""
    if backup and os.path.exists(p):
        with open(p + ".bak", "w", encoding="utf-8") as f:
            f.write(open(p, "r", encoding="utf-8").read())
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))
        f.write("\n")
    os.replace(tmp, p)


class Stream:
    """由字符串种子展开的确定性字节流。不依赖 random 模块的实现细节。"""

    def __init__(self, seed):
        self.seed = seed
        self.i = 0
        self.buf = b""

    def _more(self):
        self.buf += hashlib.sha256(("%s|%d" % (self.seed, self.i)).encode("utf-8")).digest()
        self.i += 1

    def take(self, k):
        while len(self.buf) < k:
            self._more()
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def below(self, n):
        if n <= 0:
            return 0
        k = max(1, (n.bit_length() + 7) // 8)
        return int.from_bytes(self.take(k), "big") % n

    def shuffled(self, seq):
        a = list(seq)
        for i in range(len(a) - 1, 0, -1):
            j = self.below(i + 1)
            a[i], a[j] = a[j], a[i]
        return a


# ── 数字池 ───────────────────────────────────────────────────────
def load_pool():
    if not os.path.exists(F_POOL):
        sys.exit("池还没建：先跑 python3 电波.py build")
    p = read_json(F_POOL)
    d = p["digits"]
    if sha(d) != p["sha256"]:
        sys.exit("池被改过：sha256 对不上。原产物作废前先查清楚。")
    return d


def load_table():
    if not os.path.exists(F_TABLE):
        sys.exit("表还没建：先跑 python3 电波.py build")
    t = read_json(F_TABLE)
    if sha(t["symbols"]) != t["sha256"]:
        sys.exit("表被改过：sha256 对不上。")
    if t["symbols"] != SYMBOLS:
        sys.exit("表文件和程序里写死的表不一致。")
    return t["symbols"]


# ── 造串 ─────────────────────────────────────────────────────────
MIN_DEPTH = 90  # 池浅于这个的常数暂不入签——不是排除，是它还供不起一串


def pickable(pool):
    return [n for n in ORDER if len(pool[n]) >= MIN_DEPTH]


def choose(item_seed, pool):
    """从种子推出 3 个常数 + 各自的起点 + 长度。"""
    s = Stream(item_seed)
    picked = s.shuffled(pickable(pool))[:3]
    L = 15 + s.below(26)  # 15..40

    # 轮流取位：j 从 0 到 2L-1，第 j 个数字来自 picked[j % 3] 的第 j//3 位
    need = [0, 0, 0]
    for j in range(2 * L):
        need[j % 3] += 1

    off = []
    for idx, name in enumerate(picked):
        room = len(pool[name]) - need[idx] - 1
        if room <= 0:
            sys.exit("池太短：%s 只够 %d 位" % (name, len(pool[name])))
        off.append(s.below(room))

    return picked, off, L, need


def weave(pool, coords):
    """坐标 -> 符号串。纯函数：同样的坐标永远同样的串。"""
    picked, off, L, need = coords
    digits = []
    for j in range(2 * L):
        k = j % 3
        digits.append(pool[picked[k]][off[k] + j // 3])
    out = []
    for i in range(L):
        idx = int(digits[2 * i]) * 10 + int(digits[2 * i + 1])
        out.append(SYMBOLS[idx])
    return "".join(out)


# ── 命令：build ──────────────────────────────────────────────────
def cmd_build(args):
    digits_wanted = 3000
    for i, a in enumerate(args):
        if a == "--digits":
            digits_wanted = int(args[i + 1])

    try:
        import mpmath as mp
    except ImportError:
        sys.exit("build 需要 mpmath。临时装法：\n"
                 "  python3 -m pip install --target /root/pylib mpmath\n"
                 "  PYTHONPATH=/root/pylib python3 电波.py build")
    from decimal import getcontext
    getcontext()

    old = {}
    if os.path.exists(F_POOL):
        old = read_json(F_POOL)["digits"]

    def frac(fn, n):
        """取小数部分（去掉整数位）的前 n 位。只算一次——g1 在 3000 位要两分钟，
        算两遍验不起。真伪靠下面的公开前缀自检。"""
        mp.mp.dps = n + 140
        v = fn() + 0  # 强制求值，别让懒常量骗时间
        x = v - mp.floor(v)
        k = int(mp.floor(x * mp.mpf(10) ** (n + 40)))
        return str(k).rjust(n + 40, "0")[:n]

    def g_delta(n):
        """Feigenbaum δ：超稳周期参数 + Wegstein/Aitken 加速。

        这是九个数里唯一给不深的：迭代法每多一倍周期只多约一位，
        2^14 = 16384 步、四轮 Aitken，只到十三位。数学上要更多位得解重整化方程，
        那是另一件事。所以 δ 池浅，浅的常数暂不入签。
        """
        K = 14
        mp.mp.dps = n + 200

        def step(x, r):
            return r * x * (1 - x)

        def run(x, r, steps):
            for _ in range(steps):
                x = step(x, r)
            return x

        lam = []
        for k in range(K + 1):
            if k == 0:
                guess = mp.mpf(2)
            elif k == 1:
                guess = mp.mpf("3.2")
            else:
                guess = lam[k - 1] + (lam[k - 1] - lam[k - 2]) / mp.mpf("4.669")
            g = lambda r: run(mp.mpf("0.5"), r, 1 << k) - mp.mpf("0.5")
            lam.append(mp.findroot(g, guess, solver="secant",
                                   tol=mp.mpf(10) ** (-(n + 90)), maxsteps=200))

        d = [(lam[k] - lam[k - 1]) / (lam[k + 1] - lam[k]) for k in range(1, len(lam) - 1)]
        x = d
        for _ in range(4):
            if len(x) < 4:
                break
            nx = []
            for i in range(len(x) - 2):
                den = x[i + 2] - 2 * x[i + 1] + x[i]
                nx.append(x[i] - (x[i + 1] - x[i]) ** 2 / den if den != 0 else x[i + 1])
            x = nx
        val = x[-1]
        ip = mp.floor(val)
        return str(int(mp.floor((val - ip) * mp.mpf(10) ** n))).rjust(n, "0")[:n]

    def g_champernowne(n):
        buf = []
        i = 1
        while sum(len(t) for t in buf) < n + 10:
            buf.append(str(i))
            i += 1
        return "".join(buf)[:n]

    FULL = {
        "pi": lambda: mp.pi,
        "phi": lambda: (1 + mp.sqrt(5)) / 2,
        "e": lambda: mp.e,
        "gamma": lambda: mp.euler,
        "zeta3": lambda: mp.zeta(3),
        "g1": lambda: mp.im(mp.zetazero(1)),
        "mu": lambda: mp.findroot(lambda x: mp.ei(mp.log(x)), 1.5),
    }

    if not os.path.isdir(ORIGIN):
        os.makedirs(ORIGIN)

    # 每个常数能算多深，是不一样的
    target = {n: digits_wanted for n in ORDER}
    target["delta"] = 13  # 迭代法的天花板，见 g_delta 注释

    print("=== 建池 ===")
    digits = {}
    t0 = time.time()
    for name in ORDER:
        t = time.time()
        need = target[name]
        if name in old and len(old[name]) >= need:
            digits[name] = old[name]
            print("  %-13s 沿用旧池 %5d 位" % (name, len(old[name])))
            continue
        if name == "delta":
            s = g_delta(need)
        elif name == "champernowne":
            s = g_champernowne(need)
        else:
            s = frac(FULL[name], need)
        pub = CONSTS[name][2]
        k = min(len(pub), len(s))
        if not s.startswith(pub[:k]):
            sys.exit("自检失败：%s 算出来的前几位和公开值不符\n  算出 %s\n  应为 %s"
                     % (name, s[:k], pub[:k]))
        if name in old and not s.startswith(old[name]):
            sys.exit("自检失败：%s 新算的池和旧池前缀不一致——旧池不能动" % name)
        digits[name] = s
        print("  %-13s %5d 位  %5.1fs  %s…" % (name, len(s), time.time() - t, s[:24]))

    write_json(F_POOL, {"digits": digits, "sha256": sha(digits)}, backup=True)

    write_json(F_TABLE, {"symbols": SYMBOLS, "sha256": sha(SYMBOLS),
                         "note": "定死，不改。改表＝新的一代，不是修。"}, backup=True)
    print("=== 池 %d 秒，共有 %s 位 ===" % (time.time() - t0,
                                          sum(len(v) for v in digits.values())))


# ── 命令：bang ───────────────────────────────────────────────────
def next_batch_id():
    if not os.path.isdir(ORIGIN):
        os.makedirs(ORIGIN)
    today = time.strftime("%Y%m%d")
    n = 0
    for fn in os.listdir(ORIGIN):
        if fn.startswith(today + "-"):
            n = max(n, int(fn.split("-")[1].split(".")[0]))
    return "%s-%03d" % (today, n + 1)


def cmd_bang(args):
    n_items = 20
    for i, a in enumerate(args):
        if a == "--n":
            n_items = int(args[i + 1])

    pool = load_pool()
    load_table()

    bid = next_batch_id()
    path = os.path.join(ORIGIN, bid + ".json")
    if os.path.exists(path):
        sys.exit("这批已经存在，不许覆盖：%s" % path)

    head = read_json(F_HEAD) if os.path.exists(F_HEAD) else {"seal": None, "batch": None}
    prev_seal = head.get("seal")

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    body = {
        "batch": bid,
        "stamp": stamp,
        "seed": "%s|%s" % (stamp, bid),
        "pool_sha256": sha(pool),
        "table_sha256": sha(SYMBOLS),
        "prev_seal": prev_seal,
        "gen": {"file": "tools/电波.py",
                "sha256": file_sha(os.path.abspath(__file__)),
                "python": sys.version.split()[0]},
        "items": [],
    }

    for i in range(1, n_items + 1):
        seed = "%s|%s|#%02d" % (body["seed"], bid, i)
        picked, off, L, need = choose(seed, pool)
        s = weave(pool, (picked, off, L, need))
        body["items"].append({
            "id": "%s#%02d" % (bid, i),
            "a": picked[0], "oa": off[0],
            "b": picked[1], "ob": off[1],
            "c": picked[2], "oc": off[2],
            "mode": "轮流",
            "len": L,
            "s": s,
            "sha256": sha_str(s),
        })

    body["seal"] = sha(body)
    # 不许覆盖
    if os.path.exists(path):
        sys.exit("这批已经存在，不许覆盖：%s" % path)
    write_json(path, body)
    write_json(F_HEAD, {"batch": bid, "seal": body["seal"]})

    for it in body["items"]:
        print("%s  %s@%05d %s@%05d %s@%05d  %s" % (
            it["id"],
            CONSTS[it["a"]][0], it["oa"],
            CONSTS[it["b"]][0], it["ob"],
            CONSTS[it["c"]][0], it["oc"],
            it["s"]))
    print("--- %s  seal %s ---" % (bid, body["seal"][:16]))


# ── 命令：verify ─────────────────────────────────────────────────
def cmd_verify(args):
    pool = load_pool()
    load_table()
    files = sorted(fn for fn in os.listdir(ORIGIN)
                   if fn.endswith(".json") and fn[:8].isdigit())
    if not files:
        sys.exit("还没有任何一批")
    prev = None
    bad = 0
    total = 0
    for fn in files:
        b = read_json(os.path.join(ORIGIN, fn))
        seal = b.pop("seal", None)
        if seal != sha(b):
            print("× %s 的封条对不上" % fn); bad += 1
        if b.get("prev_seal") != prev:
            print("× %s 的链断了" % fn); bad += 1
        if b.get("pool_sha256") != sha(pool):
            print("× %s 记的池和现在的池不是同一个" % fn); bad += 1
        if b.get("table_sha256") != sha(SYMBOLS):
            print("× %s 记的表和现在的表不是同一个" % fn); bad += 1
        g = b.get("gen")
        if g is None:
            print("- %s 是老批次，没记生成它的程序" % fn)
        elif g.get("sha256") != file_sha(os.path.abspath(__file__)):
            print("! %s 是另一版程序生成的（%s…），现在是 %s… —— 不算错，但要知道"
                  % (fn, g["sha256"][:8], file_sha(os.path.abspath(__file__))[:8]))
        for it in b["items"]:
            total += 1
            picked = (it["a"], it["b"], it["c"])
            off = (it["oa"], it["ob"], it["oc"])
            need = [0, 0, 0]
            for j in range(2 * it["len"]):
                need[j % 3] += 1
            s = weave(pool, (picked, off, it["len"], need))
            if s != it["s"]:
                print("× %s 重算不出来" % it["id"]); bad += 1
            if sha_str(it["s"]) != it["sha256"]:
                print("× %s 的串被改过" % it["id"]); bad += 1
        prev = seal
    print("验了 %d 批 / %d 条 -> %s" % (len(files), total,
                                       "全对" if bad == 0 else "%d 处不对" % bad))
    return 0 if bad == 0 else 1


# ── 命令：show ───────────────────────────────────────────────────
def cmd_show(args):
    if not args:
        sys.exit("用法：电波.py show <编号，例如 20261123-001#03>")
    want = args[0]
    for fn in sorted(os.listdir(ORIGIN)):
        if not (fn.endswith(".json") and fn[:8].isdigit()):
            continue
        for it in read_json(os.path.join(ORIGIN, fn))["items"]:
            if it["id"] == want:
                print(it["s"])
                return
    sys.exit("没有这条：%s" % want)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[1], argv[2:]
    if cmd == "build":
        return cmd_build(rest)
    if cmd == "bang":
        return cmd_bang(rest)
    if cmd == "verify":
        return cmd_verify(rest)
    if cmd == "show":
        return cmd_show(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
