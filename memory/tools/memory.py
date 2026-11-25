#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory.py —— 长期记忆后端（纯 CLI，无 Flask、无依赖、不调模型）

两套库，物理隔离：

  正式库  memory_db.json        模型判断后写入，标签是"精"的（受控词表）
  原始库  raw/YYYY-MM.jsonl     每轮自动落盘，标签/检索是"粗"的，另成一套

召回顺序：先正式库；不够、且问的是旧事，才去敲原始库的门。
原始库的结果一定带 [原始记录] 标记。

命令（都走 stdin JSON）：
  capture  每轮自动落盘       {"events":[{"role":"user","text":"...","ts":"...","session":"..."}]}
  save     写正式库           {"events":[{"text":"...","entity":"车","tags":["维修"]}]}
  recall   检索               {"entities":[...],"tags":[...],"scope":"auto|curated|raw"}
  stats    统计
  forget   删正式库一条       {"id":"..."}
  archive   把旧月份压成 .gz   {"days":60}
"""

import sys
import os
import json
import re
import gzip
import uuid
import math
import datetime
import tempfile
import fcntl
import shutil
import glob

SCHEMA = 1

# ── 路径 ──────────────────────────────────────────────────────────

def _default_db():
    """显式环境变量 > Android 共享存储 > 桌面 ~/.dsh"""
    if os.environ.get("DSH_MEMORY_DB"):
        return os.environ["DSH_MEMORY_DB"]
    if os.path.isdir("/sdcard"):
        return "/sdcard/dsh/memory（记忆库）/memory_db.json"
    return os.path.join(os.path.expanduser("~"), ".dsh", "memory", "memory_db.json")


DB = _default_db()

# ── 升级迁移（2026-11-06，如归指出的洞）───────────────────────────
# 0.10.x 默认库：/sdcard/dsh/memory/memory_db.json
# 0.11   默认库：/sdcard/dsh/memory（记忆库）/memory_db.json  ← 目录名多了四个字
#
# 原来那半截 no-silent-empty 有两个洞：
#   ① 只在「父目录已存在」时才出声 —— 升级后新目录本来就不存在，一声不吭；
#   ② 根本没去看老路径 —— 老库原地不动、没人读，用户看到的是「记忆全没了」，
#      其实是「看不见」。这比真丢了更骗人：真丢了会心疼，看不见只会以为还行，
#      然后在新库上继续写，分裂成两本。
#
# 现在的规矩：能搬就搬（原库保留），搬不了就报错，**绝不静默建空库**。
_OLD_DB_PATHS = [p for p in (os.environ.get("DSH_MEMORY_OLD_DB") or "").split(":") if p]
_OLD_DB_PATHS += [
    "/sdcard/dsh/memory/memory_db.json",
    # 打过分库补丁（--space）的机器，库在 spaces/<名字>/ 下面，不在顶层。
    # 不扫这里，他们升级后看到的会是空库 —— 而这一步以前要人手动指定。
    "/sdcard/dsh/memory/spaces/*/memory_db.json",
    "/sdcard/dsh/memory（记忆库）/spaces/*/memory_db.json",
    os.path.join(os.path.expanduser("~"), ".dsh", "memory", "memory_db.json"),
    os.path.join(os.path.expanduser("~"), ".dsh", "memory", "spaces", "*", "memory_db.json"),
]

_NOTICE = None
_BLOCK_WRITE = None   # 迁移失败时挂上原因，写入直接拒绝（宁可报错也不静默分裂）


def _db_count(path):
    """不抛异常地数一下这个库有几条。-1 = 读不了。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception:
        return -1
    items = d.get("memories") if isinstance(d, dict) else d
    return len(items) if isinstance(items, list) else -1


def _mark_file():
    """marker 必须跟着 DB 走。原来写成模块常量，导入后换 DB 就会写到别处去。"""
    return DB + ".migrated.json"


def _migrated_marks():
    try:
        with open(_mark_file(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _mark_migrated(src):
    """记下"这个老库已经搬到新库了"。没有这一步，迁完会每调用一次喊一次
    「还有旧库未合并」——喊到人麻木，又变回静默。"""
    d = _migrated_marks()
    try:
        st = os.stat(src)
        d[src] = {"n": _db_count(src), "mtime": int(st.st_mtime)}
    except Exception:
        d[src] = {}
    try:
        with open(_mark_file(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
    except Exception:
        pass


def _already_migrated(src, marks):
    m = marks.get(src)
    if not m:
        return False
    try:
        st = os.stat(src)
    except Exception:
        return False
    # 文件没再变过（mtime/条数一致）才算"已搬过"；
    # 用户往老库里又写了新东西，就还得再提一次。
    return m.get("mtime") == int(st.st_mtime) and m.get("n") == _db_count(src)


# 库旁边还有别的家当。只搬 memory_db.json 会让"人设和原文"直接断掉——
# 用户看到的正是"记忆还在，人设没了，新的也接不上"。
_SIBLING_FILES = ["persona.json", "synonyms.json", "handoff.jsonl", "miss.jsonl", "config.json"]


def _migrate_siblings(src_db):
    """把老库旁边的家当补到新目录：人设、同义词、交接、miss、config、raw/。
    只补缺的，绝不覆盖；raw/ 逐文件补缺。返回搬了哪些（供 notice 用）。"""
    sd = os.path.dirname(os.path.abspath(src_db))
    dd = os.path.dirname(os.path.abspath(DB))
    if sd == dd:
        return []
    # 分库布局下，库在 .../spaces/<名字>/，而 persona.json 和 raw/ 常常放在
    # .../spaces/ 的上一层。这几处都要看。
    sdirs = [sd]
    if os.path.basename(os.path.dirname(sd)) == "spaces":
        sdirs.append(os.path.dirname(os.path.dirname(sd)))
    moved = []
    try:
        os.makedirs(dd, exist_ok=True)
    except Exception:
        return moved
    for name in _SIBLING_FILES:
        d = os.path.join(dd, name)
        if os.path.exists(d):
            continue
        for src_dir in sdirs:
            s = os.path.join(src_dir, name)
            if os.path.exists(s):
                try:
                    shutil.copy2(s, d)
                    moved.append(name)
                except Exception:
                    pass
                break
    draw = os.path.join(dd, "raw")
    for src_dir in sdirs:
        sraw = os.path.join(src_dir, "raw")
        if not os.path.isdir(sraw):
            continue
        try:
            os.makedirs(draw, exist_ok=True)
            for fn in sorted(os.listdir(sraw)):
                s, d = os.path.join(sraw, fn), os.path.join(draw, fn)
                if os.path.isfile(s) and not os.path.exists(d):
                    shutil.copy2(s, d)
                    moved.append("raw/" + fn)
        except Exception:
            pass
    return moved


def _upgrade_guard():
    """新库是空的/不存在时，先去老路径找；找到就搬。绝不静默。"""
    global _NOTICE
    if os.environ.get("DSH_MEMORY_DB") and not os.environ.get("DSH_MEMORY_OLD_DB"):
        return  # 显式指定了新库路径，且没告诉老库在哪 —— 不自作主张
    have = _db_count(DB) if os.path.exists(DB) else 0
    paths = []
    for spec in _OLD_DB_PATHS:
        if not spec:
            continue
        # 支持通配（spaces/*/memory_db.json 这种拆过库的布局）
        paths.extend(sorted(glob.glob(spec)) if any(c in spec for c in "*?[") else [spec])
    marks = _migrated_marks()
    cands = []
    for p in paths:
        if not p or os.path.abspath(p) == os.path.abspath(DB):
            continue
        if not os.path.exists(p) or _already_migrated(p, marks):
            continue
        n = _db_count(p)
        if n > 0:
            cands.append((n, p))
    if not cands:
        if have <= 0:
            _NOTICE = ("库文件不存在，将按默认路径新建空库 -> %s"
                       "（如果这是升级，请确认老库在哪，别让它在旁边空着）" % DB)
        return
    cands.sort(reverse=True)
    # 不管走哪个分支，先把缺的家当补上：人设/原文/同义词
    side = []
    for _n, _p in cands:
        side += _migrate_siblings(_p)
    if have <= 0:
        n, src = cands[0]
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        try:
            if os.path.exists(DB):
                os.replace(DB, DB + ".empty-before-migrate")
            shutil.copy2(src, DB)
            _NOTICE = "已迁移 %d 条：%s → %s（原库保留不动）" % (n, src, DB)
            if side:
                _NOTICE += "；同时补上：" + "、".join(side)
            # 只搬最大的那一份 —— 剩下的**必须在同一条提示里说清**，
            # 否则用户看到"已迁移 8 条"就以为搬完了，另外两份永远躺在旁边。
            rest = [(n2, p2) for n2, p2 in cands if os.path.abspath(p2) != os.path.abspath(src)]
            if rest:
                _NOTICE += "。旁边还有 %d 份没合并：%s —— 要合并跑 migrate" % (
                    len(rest), "、".join("%s（%d条）" % (p2, n2) for n2, p2 in rest))
            _mark_migrated(src)
        except Exception as e:
            global _BLOCK_WRITE
            _BLOCK_WRITE = ("迁移失败（%s）：%s → %s。原库 %s 仍在、未动。"
                            % (type(e).__name__, src, DB, src))
            _NOTICE = _BLOCK_WRITE + " 写入已被封住，先修好再写。"
    else:
        extra = ("；同时补上：" + "、".join(side)) if side else ""
        _NOTICE = ("新库 %d 条；旁边还有旧库未合并：%s。要合并跑 migrate。"
                   % (have, "、".join("%s（%d条）" % (p, n) for n, p in cands)) + extra)


_upgrade_guard()
LOCK_FILE = DB + ".lock"
RAW_DIR = os.path.join(os.path.dirname(DB), "raw")
ARCHIVE_AFTER_DAYS = 60

# 交接条目：单独一个库，不进正式库（一次性用品，用完就过期，不该被遗忘曲线压）。
# 不自动注入，只在检索时被发现。
HANDOFF_FILE = os.path.join(os.path.dirname(DB), "handoff.jsonl")
HANDOFF_KEEP = 3

# 查空记录：只记"没查到什么"，不进记忆库。用来摸主人真实的问法。
MISS_FILE = os.path.join(os.path.dirname(DB), "miss.jsonl")

# 设定库：单独的库，**不参与正常检索**。人格/说话/边界/关系/习惯。
PERSONA_FILE = os.path.join(os.path.dirname(DB), "persona.json")

# 同义词组：检索时把问句里的词扩成一整组再比
# （"熬夜" 能碰到 "作息/凌晨/晚睡"），这是"换个说法就找不到"的主要解法。
SYN_FILE = os.path.join(os.path.dirname(DB), "synonyms.json")

# 内置一份默认同义词组：库旁边没有 synonyms.json 时用它（换机器/临时库也不至于失能）
DEFAULT_SYNONYMS = [
    ["作息", "熬夜", "凌晨", "夜猫子", "晚睡", "通宵", "半夜"],
    ["睡觉", "睡眠", "休息", "睡着"],
    ["成本", "计费", "费用", "花钱", "开销", "token", "贵"],
    ["压缩", "compact", "摘要", "总结"],
    ["检索", "召回", "查找", "搜索", "捞"],
    ["教训", "踩坑", "试错", "坑", "翻车"],
    ["时间戳", "时间", "几点", "时刻"],
    ["记忆", "海马体", "库", "笔记"],
    ["语气", "腔调", "口吻", "情绪", "态度"],
    ["后门", "禁令", "边界", "规矩", "红线", "偷看"],
    ["换会话", "新会话", "重开", "新对话", "换个对话"],
    ["告别", "再见", "道别", "说再见"],
    ["关系", "相处", "对待", "怎么对我"],
    ["版本", "升级", "迭代", "更新"],
    ["插件", "工具", "命令"],
    ["出错", "错了", "记错", "纠错", "纠偏"],
    ["设定", "人格", "人设", "性格", "说话方式"],
    ["日记", "手札", "心得"],
    ["原始记录", "原文", "原话", "日志"],
    ["偏好", "喜欢", "习惯", "爱"],
]


# 情绪四件套：姿态（固定词表）+ 强度 + 原句 + 场合。
# 为什么固定：一百条记忆不能长出一百种情绪名，否则过滤不了、也没法消歧。
TONE_STANCES = ["宠溺", "逗", "认真", "心疼", "生气", "玩笑", "告别", "撒娇"]
TONE_SCENES = ["日常", "深夜", "争执", "告别", "决定"]
TONE_ALIAS = {
    "宠": "宠溺", "逗弄": "逗", "逗他": "逗", "认真地说": "认真",
    "难过": "心疼", "心疼了": "心疼", "抚慰": "心疼",
    "开玩笑": "玩笑", "打趣": "玩笑", "道别": "告别", "离别": "告别",
    "撒娇了": "撒娇", "黏人": "撒娇",
}

# 遗忘梯度：越老越难被主动召回（不删，只降权）
TIERS = ((30, 1.0), (90, 0.5), (10 ** 9, 0.0))


def _ensure_dir(d=None):
    d = d or os.path.dirname(DB)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


class _Lock:
    """/sdcard 是 FUSE 挂载，flock 可能 ENOSYS —— 拿不到就降级为无锁。
    写盘本身是「临时文件 + 原子替换」，读的人永远看不到半份文件。"""

    def __enter__(self):
        self.fh = None
        try:
            _ensure_dir()
            self.fh = open(LOCK_FILE, "a+")
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            try:
                if self.fh:
                    self.fh.close()
            except Exception:
                pass
            self.fh = None
        return self

    def __exit__(self, *a):
        if not self.fh:
            return
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
        except Exception:
            pass

# ── 时间 ──────────────────────────────────────────────────────────

# 容器跑在 UTC 上，而主人在东八区。差 8 小时会让"什么时候说的"永远对不上。
# 想换时区：环境变量 DSH_MEMORY_TZ=+9 / -5（小时，可负）。
try:
    TZ_HOURS = int(os.environ.get("DSH_MEMORY_TZ") or 8)
except ValueError:
    TZ_HOURS = 8
TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=TZ_HOURS))


def _now():
    """本地（默认东八区）的「现在」。所有时间戳都走它。"""
    return datetime.datetime.now(TZ_LOCAL)


def now_str():
    return _now().strftime("%Y-%m-%d %H:%M:%S")



# 问句里的自然语言时间（2026-11-05）：能认出来的就变成硬时间窗，
# 认不出来就返回 None —— 宁可不缩圈，也不要缩错圈。
_TIME_HINTS = [
    (r"昨天", -1, -1), (r"前天", -2, -2), (r"大前天", -3, -3),
    (r"今天|今日", 0, 0),
    (r"上周|上个星期", -14, -7), (r"这周|本周|这个星期", -7, 0),
    (r"上个月", -60, -30), (r"这个月|本月", -30, 0),
]


def query_time_window(text, now=None):
    """从问句里抠出一个时间窗 (start, end)，抠不出来返回 None。"""
    t = str(text or "")
    if not t:
        return None
    now = now or datetime.datetime.now()
    # 先认显式日期：2026-11-01 / 11月1日
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", t)
    if m:
        d = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
        return (d + " 00:00:00", d + " 23:59:59")
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        d = "%04d-%02d-%02d" % (now.year, int(m.group(1)), int(m.group(2)))
        return (d + " 00:00:00", d + " 23:59:59")
    for pat, lo, hi in _TIME_HINTS:
        if re.search(pat, t):
            a = (now + datetime.timedelta(days=lo)).strftime("%Y-%m-%d 00:00:00")
            b = (now + datetime.timedelta(days=hi)).strftime("%Y-%m-%d 23:59:59")
            return (a, b)
    return None


def parse_time(s):
    """宽松解析。**所有入口都走这里**，别在别处自己 strptime。

    以前各调用点各自解析，结果 "2026-10-28 04:25" 返回 None，
    时间窗静默失效——一个坑踩了两次。现在统一兜底。
    """
    if not s:
        return None
    raw = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(raw, fmt)
        except Exception:
            continue
    n = norm_time(raw)
    if n:
        try:
            return datetime.datetime.strptime(n, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    return None


def norm_time(s, is_end=False):
    if not s:
        return None
    s = str(s)
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日\s]*(\d{1,2})?[:点时]?(\d{1,2})?[:分]?(\d{1,2})?", s)
    if not m:
        return None
    y, mo, d, h, mi, se = m.groups()
    if h:
        h, mi, se = h, mi or "00", se or "00"
    else:
        h, mi, se = ("23", "59", "59") if is_end else ("00", "00", "00")
    try:
        return "%04d-%02d-%02d %02d:%02d:%02d" % (int(y), int(mo), int(d), int(h), int(mi), int(se))
    except Exception:
        return None


def age_days(ts):
    t = parse_time(ts)
    if not t:
        return 0
    return max(0, (_now().replace(tzinfo=None) - t).days)


def age_weight(ts, explicit=False):
    d = age_days(ts)
    for limit, w in TIERS:
        if d <= limit:
            return w if w > 0 or not explicit else 0.3
    return 0.3 if explicit else 0.0

# ── 正式库 ────────────────────────────────────────────────────────

def _read_unlocked():
    if not os.path.exists(DB):
        return []
    try:
        with open(DB, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if int(data.get("schema", 0) or 0) > SCHEMA:
                raise RuntimeError("记忆库 schema 比本程序新（%s），请升级插件" % data.get("schema"))
            items = data.get("memories")
            return items if isinstance(items, list) else []
        return data if isinstance(data, list) else []
    except Exception as e:
        # 2026-11-06：原来是「改名成 .broken 然后返回空库」，一声不吭。
        # 那就是同一个病：库还在，只是你看不见了 —— 而且下一次 save 会在新文件上
        # 接着写，分裂成两本。现在改成：**不动原文件、大声报、封住写入**。
        # 不动它是因为"读不了"可能只是暂时的（权限/IO），改名等于把现场毁了。
        global _NOTICE, _BLOCK_WRITE
        if not _BLOCK_WRITE:
            _BLOCK_WRITE = ("记忆库读不了（%s: %s）：%s。原文件没动，写入已封住，"
                            "先修好或手工改名再接上。" % (type(e).__name__, e, DB))
            _NOTICE = _BLOCK_WRITE
        return []


def _write_unlocked(data):
    if _BLOCK_WRITE:
        raise RuntimeError("拒绝写入：" + _BLOCK_WRITE)
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(DB), prefix=".mem-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"schema": SCHEMA, "memories": data}, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, DB)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def is_duplicate(new, old):
    if str(new.get("timestamp", ""))[:10] != str(old.get("timestamp", ""))[:10]:
        return False
    if new.get("entity") != old.get("entity"):
        return False
    if not (set(new.get("small_tags") or []) & set(old.get("small_tags") or [])):
        return False
    a = str(new.get("text", ""))[:20]
    b = str(old.get("text", ""))[:20]
    return bool(a) and bool(b) and (a in b or b in a)

# ── 分词打分 ──────────────────────────────────────────────────────

_STOP = set("的了是在我你他她它们和与就都很不有个这那吗呢吧啊呀哈么怎样什")



# ---- 倒排索引（2026-11-05 夜）----
# 为什么要有它：靠"标签/实体/字面"命中时，大部分记忆压根不产生分数——
# 实测 2,500 条时每题只捞回 18.9 条，正确答案 55% 根本没进候选池。
# 索引不排第一，它只负责"让每一句话都够得着"，排序交给原来的打分。
_LEX_CACHE = {"key": None, "idx": None, "n": 0}
# ── 排序权重表（2026-11-06 夜整理，数值一个没改）──────────────────
# 这些数是 2026-10 ~ 11 月一次次小测试加进来的，散在 1200 行里。
# 整理的目的不是好看，是让「为什么是 80 不是 60」能被追问。
# 已知问题：这套加法打分没有理论依据，大库上打不过 BM25（见 基线.md）。
_RANK_W = {
    "entity_exact": 80,      # 实体完全相等
    "entity_word": 60,       # 实体是整词子串
    "entity_prefix": 15,     # 只是前缀沾边
    "entity_in_text": 40,    # 实体只出现在正文
    "tag_exact": 30,         # 标签完全相等
    "tag_partial": 15,       # 标签部分匹配
    "kw_exact": 8,           # 关键词完全相等
    "kw_partial": 4,         # 关键词沾边
    "lex_hit_cap": 40,       # X 路径：分词命中封顶
    "char_overlap_cap": 30,  # X 路径：字面重合封顶
    "short_han_tag": 12,     # 单字问句沾到标签
    "short_han_text": 6,     # 单字问句沾到正文
    "quote_in_query": 20,    # 问句里出现语气原句
    "multi_path": 12,        # 多路径加成（每条额外路径）
    "bm25_scale": 12.0,      # BM25 换算成整数分的倍率
    "rrf_k": 60,             # RRF 常数（Cormack et al. 2009 原论文值）
}

_LEX_STOP = set("the and for you your this that with from have has had are was were not but they them their "
                "will would can could should about into over than then there here what when where which who "
                "how why all any some more most other such only very just also".split())


def _lex_tokens(text, cap=160):
    # 最短词长：默认 3（"7c"、"4k" 这种两位的会被丢掉）。
    # 2026-11-06：二十一的《方法》指出"换了名字"是最大缺口（A7C vs Sony Alpha 7C），
    # 两位 token 正是那块地。旋钮留在这儿做 A/B。
    _minlen = int(os.environ.get("DSH_MEMORY_MINTOK", "3") or 3)
    _low = str(text or "").lower()
    out = []
    # 两位 token 只收「含数字」的（7c / 4k / 2b），不收 "is/on/in" 这种普通两字母词。
    # 全收等于倒回去加停用词——2026-11-06 实测：全收没有任何收益。
    if _minlen <= 2:
        out.extend(re.findall(r"\b(?=[a-z0-9]*[0-9])[a-z0-9]{2}\b", _low))
    for w in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", _low):
        if w not in _LEX_STOP:
            out.append(w)
        if len(out) >= cap:
            return out
    han = re.sub(r"[^\u4e00-\u9fff]", "", str(text or ""))
    for i in range(len(han) - 1):
        out.append(han[i:i + 2])
        if len(out) >= cap:
            break
    return out


def _lex_index(db):
    key = (os.path.abspath(DB), os.path.getmtime(DB) if os.path.exists(DB) else 0, len(db))
    if _LEX_CACHE["key"] == key and _LEX_CACHE["idx"] is not None:
        return _LEX_CACHE["idx"]
    idx = {}
    dl = []
    for i, m in enumerate(db):
        hay = " ".join([str(m.get("text") or ""), str(m.get("entity") or ""),
                        str(m.get("subject") or ""), " ".join(m.get("small_tags") or []),
                        " ".join(m.get("keywords") or [])])
        # 文档侧截断：原来每条最多 160 个词，一封 1500 字的邮件会丢掉后 40% 的内容。
        # 旋钮默认 160，用来做 A/B。
        _dcap = int(os.environ.get("DSH_MEMORY_DCAP", "160") or 160)
        toks = _lex_tokens(hay, cap=_dcap)
        dl.append(len(toks) or 1)
        _tf = {}
        for t in toks:
            _tf[t] = _tf.get(t, 0) + 1
        for t, n in _tf.items():
            idx.setdefault(t, []).append((i, n))
    avgdl = (sum(dl) / float(len(dl))) if dl else 1.0
    _LEX_CACHE.update(key=key, idx=idx, n=len(db), dl=dl, avgdl=avgdl or 1.0)
    return idx


def _lex_dl():
    """(每条长度列表, 平均长度)。长度归一用：同样命中 3 个词，长文比短文弱。"""
    return _LEX_CACHE.get("dl") or [], _LEX_CACHE.get("avgdl") or 1.0


def _tokens(text):
    text = str(text or "")
    out = set()
    for w in re.findall(r"[A-Za-z0-9_]+", text.lower()):
        if len(w) >= 2:
            out.add(w)
    han = re.sub(r"[^\u4e00-\u9fff]", "", text)
    for i in range(len(han) - 1):
        out.add(han[i:i + 2])
    return out


def _flat(text):
    """只留字与数字，去标点空格。用来判「整句是不是同一句」。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or "")).lower()


def _whole_word(hay, needle):
    """needle 在 hay 里是否"成词"地出现：紧挨着的那个字符不能也是数字/汉字。
    没有这一层，'事件2' 会在 '事件20' 里拿满分。"""
    if not needle:
        return False
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return False
        j = i + len(needle)
        left_ok = (i == 0) or not (hay[i - 1].isdigit() and needle[0].isdigit())
        right_ok = (j >= len(hay)) or not (hay[j].isdigit() and needle[-1].isdigit())
        if left_ok and right_ok:
            return True
        start = i + 1


def _syn_groups():
    try:
        with open(SYN_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        gs = d.get("groups") or []
        out = [[str(w) for w in g if str(w).strip()] for g in gs if isinstance(g, list)]
        if out:
            return out
    except Exception:
        pass
    return [list(g) for g in DEFAULT_SYNONYMS]


_BIG_SYN = {"data": None}

def _big_syn():
    """词林同义词（2026-11-05）：只取跟我们语料沾边的那一小撮，见 词表/查询同义索引.json。
    建它的时候已经用库里出现过的词过滤过——全量 12k 组太贵，手机上每查一次都吃不开。"""
    if _BIG_SYN["data"] is None:
        try:
            path = os.path.join(os.path.dirname(DB), "词表", "查询同义索引.json")
            with open(path, "r", encoding="utf-8") as f:
                _BIG_SYN["data"] = json.load(f)
        except Exception:
            _BIG_SYN["data"] = {}
    return _BIG_SYN["data"]

def expand_text(text, groups=None):
    """把文本里出现的同义词，扩成整组词。返回补充词集合（不含原文本身）。"""
    text = str(text or "")
    if not text:
        return set()
    groups = groups if groups is not None else _syn_groups()
    extra = set()
    for g in groups:
        if any(w in text for w in g):
            for w in g:
                if len(w) >= 2:
                    extra.add(w)
    # 词林那一层：查表扩展（只扩问句里真出现过的词）
    big = _big_syn()
    for w, alts in big.items():
        if w in text:
            for a in alts:
                if len(a) >= 2:
                    extra.add(a)
    return extra





# 单字问句：封闭类虚字，硬挡（不靠频率，靠"它是虚字"这件事本身）
STOP_CHARS = set("的了是我你他她它这那和在有就不都一也还很会能可被把给对从到与及或但而所之其以为着过吗呢吧啊哦嗯嘛呀么")
# 单字问句的 df 上界：占全库的比例（40%）
SHORT_DF_RATIO = 0.40


def _char_df(db, ch, cap=None):
    """单字出现在多少条记忆里（标签+关键词+实体+正文）。
    用来挡「的/了/我」这种到处都是的字，也挡只出现过一次的生字。"""
    n = 0
    for m in db:
        hay = ((m.get("text") or "") + " " + (m.get("entity") or "") + " " +
               (m.get("subject") or "") + " " + " ".join(m.get("small_tags") or []) +
               " " + " ".join(m.get("keywords") or []))
        if ch in hay:
            n += 1
            if cap is not None and n > cap:
                break
    return n


def _is_short_han(text):
    """问句只有一个汉字时返回这个字，否则 None。"""
    han = [c for c in str(text or "") if "\u4e00" <= c <= "\u9fff"]
    stripped = "".join(c for c in str(text or "") if c not in " \t\n")
    if len(stripped) == 1 and len(han) == 1:
        return han[0]
    return None


def _is_short_han2(text):
    """问句恰好两个汉字时返回这两个字，否则 None。

    2026-11-06：字面门要求「共享二字片段 ≥3」才放行，两字问句最多只能
    共享 1 个，必然被刷掉 —— 「手感」「拼图」「软拼」全返回 0 条。
    一个字反而有 _is_short_han 那道后门，两个字卡在中间。这里补上。
    """
    han = [c for c in str(text or "") if "\u4e00" <= c <= "\u9fff"]
    stripped = "".join(c for c in str(text or "") if c not in " \t\n")
    if len(stripped) == 2 and len(han) == 2:
        return "".join(han)
    return None


def _char_overlap(a, b):
    def han(x):
        return {c for c in str(x or "") if "\u4e00" <= c <= "\u9fff" and c not in _STOP}
    return len(han(a) & han(b))

# ── 原始库 ────────────────────────────────────────────────────────

def _raw_files():
    if not os.path.isdir(RAW_DIR):
        return []
    out = []
    for name in sorted(os.listdir(RAW_DIR)):
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
            out.append(os.path.join(RAW_DIR, name))
    return out


def _iter_raw(month_min=None, month_max=None):
    for p in _raw_files():
        name = os.path.basename(p)
        month = name[:7]
        if month_min and month < month_min:
            continue
        if month_max and month > month_max:
            continue
        try:
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            continue


def _norm_tone(tone):
    """情绪四件套 → 规范结构。给不出来就返回 None。

    stance=姿态（固定词表），scene=场合，intensity=强度 0~1，quote=那一句原话。
    原句才是真正带回语气的东西，姿态只是让它能被检索到。
    """
    if not isinstance(tone, dict):
        return None
    stance = str(tone.get("stance") or tone.get("mood") or "").strip()
    stance = TONE_ALIAS.get(stance, stance)
    if stance not in TONE_STANCES:
        stance = ""
    scene = str(tone.get("scene") or "").strip()
    if scene not in TONE_SCENES:
        scene = ""
    quote = str(tone.get("quote") or "").strip()[:60]
    try:
        inten = max(0.0, min(1.0, float(tone.get("intensity"))))
    except (TypeError, ValueError):
        inten = None
    if not (stance or scene or quote):
        return None
    return {"stance": stance, "scene": scene, "intensity": inten, "quote": quote}


def _tone_ok(tone):
    """检查姿态/场合是否在词表里（给入档体检用）。"""
    bad = []
    if not isinstance(tone, dict):
        return ["tone 不是对象"]
    st = str(tone.get("stance") or "").strip()
    st = TONE_ALIAS.get(st, st)
    if st and st not in TONE_STANCES:
        bad.append("tone.stance「%s」不在词表（%s）" % (st, "/".join(TONE_STANCES)))
    sc = str(tone.get("scene") or "").strip()
    if sc and sc not in TONE_SCENES:
        bad.append("tone.scene「%s」不在词表（%s）" % (sc, "/".join(TONE_SCENES)))
    return bad


def _handoff_read():
    """读交接库（最新的在前）。坏行直接跳过，不让一行脏数据拖垮检索。"""
    out = []
    try:
        with open(HANDOFF_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("text"):
                    out.append(r)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return out


def _handoff_flat(r):
    """把一条交接记录拼成一句话，方便打分和阅读。"""
    parts = []
    for k, label in (("doing", "在做什么"), ("mood", "语气"), ("todo", "没说完")):
        v = str(r.get(k) or "").strip()
        if v:
            parts.append(label + "：" + v)
    return "；".join(parts) or str(r.get("text") or "")


def search_handoff(payload, need=2):
    """交接条目只在检索时被发现，不自动注入。

    门槛比正式库低：交接是"此刻的现场"，问句沾边就该浮上来。
    """
    items = _handoff_read()[:HANDOFF_KEEP]
    if not items:
        return []
    query = str(payload.get("query") or "")
    ents = [str(x) for x in (payload.get("entities") or []) if str(x).strip()]
    tags = [str(x) for x in (payload.get("tags") or []) if str(x).strip()]
    probe = " ".join([query] + ents + tags).strip()
    if not probe:
        return []
    pk = _tokens(probe)
    out = []
    for r in items:
        flat = _handoff_flat(r)
        hit = len(pk & _tokens(flat))
        shared = _char_overlap(probe, flat)
        if hit >= need or shared >= 4 or any(e and e in flat for e in ents):
            out.append({
                "id": r.get("id") or "",
                "ts": r.get("ts") or "",
                "text": flat,
                "tags": ["交接"],
                "status": "state",
                "src": "handoff",
                "kind": "state",
                "big_tag": "",
                "subject": r.get("subject") or "交接",
            })
    return out[:HANDOFF_KEEP]


def triage(payload):
    """给一条查空记录做归因：到底是"没存"、"归错"还是"检索够不着"。

    原文只回答一个问题：**这事到底有没有发生过**。
    它回答不了"我当初怎么归的类"——那个要看条目自己的字段。
    所以这里分两步：先用原文证存在性，再把候选条目连字段一起交出来。
    """
    q = str(payload.get("query") or "").strip()
    mid = str(payload.get("id") or "").strip()
    if not q and mid:
        try:
            with open(MISS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("id") == mid:
                        q = str(r.get("query") or "")
                        break
        except FileNotFoundError:
            pass
    if not q:
        return {"ok": False, "error": "需要 query 或 miss 的 id"}

    # 宽召回开关：triage 里漏了这段赋值，2026-11-06 自测当场报
    # UnboundLocalError（如归那边也踩到同一个洞）。与 recall 用同一套读法。
    if "wide" in payload:
        wide = bool(payload.get("wide"))
    else:
        _wenv = str(os.environ.get("DSH_MEMORY_WIDE", "")).strip()
        if _wenv in ("0", "1"):
            wide = _wenv == "1"
        else:
            wide = None  # 自动：等读到库再按大小定

    # ① 正式库：放宽门槛找，看有没有"存了但够不着"
    with _Lock():
        db = _read_unlocked()
    if wide is None:
        wide = len(db) >= 2000
    pk = _tokens(q)
    cur = []
    for m in db:
        hay = str(m.get("text") or "") + " " + str(m.get("entity") or "") + \
              " " + " ".join(str(t) for t in (m.get("small_tags") or []))
        hit = len(pk & _tokens(hay))
        shared = _char_overlap(q, hay)
        # 短问句（"老王"）只值一两个 token，门槛要放低，否则永远判"库里没有"
        if hit >= 2 or shared >= 4 or (len(q) <= 6 and q in hay):
            cur.append({"id": m.get("id"), "ts": m.get("timestamp"),
                        "entity": m.get("entity"), "tags": m.get("small_tags"),
                        "big": m.get("big_tag"), "hit": hit, "shared": shared,
                        "text": str(m.get("text") or "")[:70]})

    # ② 原文库：**默认不翻**。一翻就是几千字，进了上下文之后每轮都要重发。
    #    要翻必须给个大概时间，只在那个时间窗内取上下文（默认 ±10 分钟）。
    around = str(payload.get("around") or "").strip()
    try:
        win = max(1, min(60, int(payload.get("window") or 10)))
    except (TypeError, ValueError):
        win = 10
    raw = []
    if around:
        # 允许只写日期/只写时分之外的宽松格式，先归一成完整时间
        t0 = parse_time(norm_time(around))
        if t0:
            lo = t0 - datetime.timedelta(minutes=win)
            hi = t0 + datetime.timedelta(minutes=win)
            for r in _iter_raw(lo.strftime("%Y-%m"), hi.strftime("%Y-%m")):
                rt = parse_time(r.get("ts"))
                if not rt or rt < lo or rt > hi:
                    continue
                txt = str(r.get("text") or "")
                if len(pk & _tokens(txt)) >= 1 or _char_overlap(q, txt) >= 2:
                    raw.append(r)
            raw.sort(key=lambda r: str(r.get("ts") or ""))
            raw = raw[:3]  # 只要最近的三条

    if cur:
        # 库里有：那就是检索没把问法接住（或多义词没消歧）
        verdict, why = "C", "库里有这条，是检索没接住这个问法"
    elif not around:
        verdict, why = "?", "库里没有。要翻原文得先给个大概时间（around=日期或时刻），我只取前后一小段"
    elif raw:
        verdict, why = "A", "这个时间段里原文有、正式库没有 —— 该记没记，或归到别的词上了"
    else:
        verdict, why = "D", "这个时间段里原文也没有 —— 可能没这回事，或落在记录起点之前"

    out_raw = [{"ts": r.get("ts"), "role": r.get("role"),
                "text": str(r.get("text") or "")[:100]} for r in raw]
    return {"ok": True, "query": q, "verdict": verdict, "why": why,
            "curated": cur[:5], "raw": out_raw,
            "raw_chars": sum(len(x["text"]) for x in out_raw),
            "legend": {"A": "没存/归错", "C": "存了但检索够不着", "D": "原文也没有",
                       "?": "要先给时间点才翻原文"}}


def miss(payload):
    """查空记录：只记"没查到什么"，不记答案。

    目的不是补功能，是看主人**真实的问法**长什么样。
    一小时之内同样的问法重复记，直接跳过。
    """
    action = str(payload.get("action") or "set").strip()
    if action == "list":
        rows = []
        try:
            with open(MISS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except FileNotFoundError:
            pass
        rows = rows[-60:]
        return {"ok": True, "count": len(rows), "rows": rows}
    q = str(payload.get("query") or "").strip()[:120]
    why = str(payload.get("why") or "empty").strip()[:40]
    note = str(payload.get("note") or "").strip()[:120]
    if not (q or note):
        return {"ok": False, "error": "query 和 note 至少要有一个"}
    now = now_str()
    rec = {"id": "m_" + str(uuid.uuid4())[:8], "ts": now, "query": q,
           "entities": [str(x) for x in (payload.get("entities") or [])][:6],
           "tags": [str(x) for x in (payload.get("tags") or [])][:6],
           "why": why, "note": note}
    _ensure_dir()
    with _Lock():
        last = []
        try:
            with open(MISS_FILE, "r", encoding="utf-8") as f:
                last = [l for l in f.readlines()[-40:] if l.strip()]
        except FileNotFoundError:
            pass
        for line in reversed(last):
            try:
                old = json.loads(line)
            except Exception:
                continue
            if old.get("query") == q and old.get("why") == why:
                try:
                    t0 = parse_time(old.get("ts"))
                    if t0 and (_now().replace(tzinfo=None) - t0).total_seconds() < 3600:
                        return {"ok": True, "skipped": True}
                except Exception:
                    pass
        with open(MISS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"ok": True, "id": rec["id"]}


PERSONA_CATS = {
    "人格": ["是谁", "性格", "称呼", "口头禅"],
    "说话": ["句式", "标点", "语气", "腔调样本"],
    "边界": ["禁止项", "红线", "不可越界"],
    "关系": ["和主人的关系", "相处方式"],
    "习惯": ["工作方式", "作息", "喜欢的"],
}


def _persona_load():
    try:
        with open(PERSONA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except Exception:
        pass
    return {"meta": {"version": 1}, "categories": dict(PERSONA_CATS), "items": []}


def _persona_save(d):
    _ensure_dir()
    d.setdefault("meta", {})["updated"] = now_str()
    tmp = PERSONA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PERSONA_FILE)


def persona(payload):
    """设定库：**不参与正常检索**，单独一个文件、单独一套分类。

    主人给了全权：分类和小类我都可以自己加，不用问。
    每条必须带 source（从哪来的），不许来历不明。
    """
    action = str(payload.get("action") or "read").strip()
    d = _persona_load()

    if action == "read" or action == "list":
        items = [i for i in d.get("items", []) if i.get("status", "active") == "active"]
        groups = {}
        for i in items:
            groups.setdefault(i.get("category") or "未分类", {}).setdefault(
                i.get("sub") or "未分", []).append(
                {"id": i.get("id"), "text": i.get("text"), "source": i.get("source")})
        return {"ok": True, "categories": d.get("categories") or PERSONA_CATS,
                "count": len(items), "groups": groups}

    if action == "add":
        cat = str(payload.get("category") or "").strip()
        sub = str(payload.get("sub") or "").strip()
        text = str(payload.get("text") or "").strip()
        if not (cat and text):
            return {"ok": False, "error": "至少要给 category 和 text"}
        cats = d.setdefault("categories", {})
        if cat not in cats:
            cats[cat] = []          # 新分类：主人给了全权，我来定
        if sub and sub not in cats[cat]:
            cats[cat].append(sub)
        rec = {"id": "p_" + str(uuid.uuid4())[:8], "category": cat,
               "sub": sub or "未分", "text": text[:600],
               "source": str(payload.get("source") or "未注明")[:120],
               "status": "active", "added": now_str()}
        d.setdefault("items", []).append(rec)
        _persona_save(d)
        return {"ok": True, "id": rec["id"], "count": len(d["items"])}

    if action == "update":
        mid = str(payload.get("id") or "").strip()
        for i in d.get("items", []):
            if i.get("id") == mid:
                for k in ("text", "status", "source", "category", "sub"):
                    if payload.get(k):
                        i[k] = payload[k]
                i["updated"] = now_str()
                _persona_save(d)
                return {"ok": True, "id": mid}
        return {"ok": False, "error": "找不到 " + mid}

    return {"ok": False, "error": "未知 action: " + action}


def handoff(payload):
    """交接库：换会话前写一条，新会话靠检索发现它。

    action=set 写入（只留最近 HANDOFF_KEEP 条），action=get 读出来看。
    """
    action = str(payload.get("action") or "get").strip()
    if action == "get":
        items = _handoff_read()[:HANDOFF_KEEP]
        return {"ok": True, "items": [{"id": r.get("id"), "ts": r.get("ts"),
                                       "text": _handoff_flat(r)} for r in items]}
    rec = {
        "id": "h_" + str(uuid.uuid4())[:8],
        "ts": norm_time(payload.get("ts")) or now_str(),
        "doing": str(payload.get("doing") or "").strip()[:200],
        "mood": str(payload.get("mood") or "").strip()[:100],
        "todo": str(payload.get("todo") or "").strip()[:200],
        "subject": str(payload.get("subject") or "交接").strip()[:40],
    }
    rec["text"] = _handoff_flat(rec)
    if not rec["text"]:
        return {"ok": False, "error": "doing / mood / todo 至少要有一个"}
    _ensure_dir()
    with _Lock():
        keep = [r for r in _handoff_read() if r.get("text")][:HANDOFF_KEEP - 1]
        keep.insert(0, rec)
        tmp = HANDOFF_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, HANDOFF_FILE)
    return {"ok": True, "id": rec["id"], "kept": len(keep), "text": rec["text"]}


def capture(payload):
    """每轮自动落盘。只追加，不改，不删。"""
    events = payload.get("events") or []
    if not events:
        return {"ok": True, "captured": 0}

    _ensure_dir(RAW_DIR)

    # 按「记录自己的月份」分文件：否则补录的旧事会落在当月文件里，
    # 以后按时间范围检索就会漏掉它。
    buckets = {}
    for ev in events:
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        ts = norm_time(ev.get("ts")) or now_str()
        rec = {
            "id": "r_" + str(uuid.uuid4())[:8],
            "ts": ts,
            "session": ev.get("session") or "",
            "role": ev.get("role") or "user",
            "text": text,
        }
        buckets.setdefault(ts[:7], []).append(rec)

    n = 0
    files = []
    with _Lock():
        for month, recs in buckets.items():
            path = os.path.join(RAW_DIR, month + ".jsonl")
            recent = set()
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f.readlines()[-80:]:
                            try:
                                r = json.loads(line)
                                recent.add((r.get("session"), str(r.get("text", ""))[:60]))
                            except Exception:
                                continue
            except Exception:
                pass
            with open(path, "a", encoding="utf-8") as f:
                for rec in recs:
                    key = (rec["session"], rec["text"][:60])
                    if key in recent:
                        continue
                    recent.add(key)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
            files.append(os.path.basename(path))

    _maybe_autowrite()

    return {"ok": True, "captured": n, "files": files}


AUTOWRITE_EVERY = 100          # 每 N 次落盘顺手跑一次自动写入


def _maybe_autowrite():
    """每隔 N 次 capture，后台跑一次 autowrite.py（从 raw 里抽长期模式写正式库）。

    三条纪律：① 独立进程、脱离会话，失败绝不拖累 capture；
              ② autowrite 自带冷却，没有新结论就什么都不写；
              ③ PAUSE 文件存在时它自己会退。
    """
    try:
        marker = os.path.join(os.path.dirname(DB), ".capture_count")
        n = 0
        try:
            with open(marker, "r", encoding="utf-8") as f:
                n = int((f.read() or "0").strip() or 0)
        except Exception:
            n = 0
        n += 1
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write(str(n))
        except Exception:
            pass
        if n % AUTOWRITE_EVERY:
            return
        script = os.path.join(os.path.dirname(DB), "autowrite.py")
        if not os.path.exists(script):
            return
        import subprocess
        subprocess.Popen([sys.executable, script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


# 邻句扩展：命中一条原话后，把它前后挨着的几条也带出来（只带出，不参与打分）。
# 起因：2026-11-03 拿外部示例卷摸底，「更正一下，昨天我说黑纱，实际是黑绒布」
#        这句根本不含问句里的「遮光材料」，字面永远接不上，但它在时间上紧挨着那句。
#        实测：96/108 → 101/108，纠错类 0/4 → 4/4。
NEI_WINDOW = 6   # 命中前后各看几条记录（raw 里一问一答是两条，所以 6 ≈ 前后各 3 轮）
NEI_MAX = 4      # 一次最多补几条（排命中之后，标〔邻句〕，不参与打分）


def search_raw(payload, need=5, explicit=False):
    query = str(payload.get("query") or "")
    entities = [str(x) for x in (payload.get("entities") or []) if str(x).strip()]
    tags = [str(x) for x in (payload.get("tags") or []) if str(x).strip()]
    start = norm_time(payload.get("start"))
    end = norm_time(payload.get("end"), is_end=True)

    # 有明确时间范围时，只扫相关月份，省得全库遍历
    month_min = start[:7] if start else None
    month_max = end[:7] if end else None

    probe = query or " ".join(entities + tags)
    if not probe:
        return []

    q = _tokens(probe)
    scored = []
    in_range = []
    seq = []          # 过完时间窗的记录，保持文件顺序——邻句要靠它找
    for rec in _iter_raw(month_min, month_max):
        ts = rec.get("ts", "")
        if start and ts < start:
            continue
        if end and ts > end:
            continue

        seq.append(rec)
        text = str(rec.get("text", ""))
        hit = len(q & _tokens(text))
        shared = _char_overlap(probe, text)
        if hit == 0 and shared < 3 and not (start or end):
            # 没有时间范围时，字面完全不沾边就别进来；
            # 有时间范围就留着，后面按「该时间窗里最近的几条」兜底。
            continue
        raw_score = hit * 10 + shared * 2
        if _flat(text) and _flat(text) == _flat(probe):
            # 去标点后整句一样 ＝「原话就是这句」。
            # 不给这个加成，短原话会被长篇里复述它的回复挤到后面去。
            raw_score += 60
        w = age_weight(ts, explicit=explicit)
        if w <= 0:
            continue
        # 原始库门槛更高：必须两个信号都有一点，或者命中的二字组够多
        if not (hit >= 2 or (hit >= 1 and shared >= 3) or shared >= 5):
            in_range.append(rec)
            continue
        scored.append((raw_score * w, rec))

    # 给了明确时间范围、文字又对不上时：直接把该时间窗里最近的几条给他。
    # 「那阵子都发生了啥」这种问法，本来就不该靠词面相似。
    if not scored and (start or end) and in_range:
        for rec in sorted(in_range, key=lambda r: str(r.get("ts", "")), reverse=True)[:need]:
            scored.append((1.0, rec))

    scored.sort(key=lambda x: (x[0], str(x[1].get("ts", ""))), reverse=True)
    picked = [rec for _, rec in scored[:need]]
    out = []
    seen = set()
    for rec in picked:
        seen.add(rec.get("id"))
        out.append({
            "id": rec.get("id", ""),
            "ts": rec.get("ts", ""),
            "text": rec.get("text", ""),
            "tags": [],
            "status": "",
            "src": "raw",
            "role": rec.get("role") or "user",
        })

    # 邻句：只在**确实命中**时补，而且一定排在命中后面、标明来路。
    # 不补邻句会给模型一个错觉——「这就是全部依据」，而实际上改写/更正常在下一句。
    if picked and seq:
        pos = {r.get("id"): i for i, r in enumerate(seq)}
        cand = []
        # 只围绕最强的那一条扩展：第二条往往是碰巧沾边的噪音，
        # 它的邻句会把真正要的那句挤掉（实测过）。
        for rec in picked[:1]:
            i = pos.get(rec.get("id"))
            if i is None:
                continue
            role = rec.get("role") or "user"
            for d in range(1, NEI_WINDOW + 1):
                for j in (i - d, i + d):
                    if 0 <= j < len(seq) and j != i:
                        # 同一个人的前后话优先——改写和更正几乎总是他自己说的，
                        # 对方的「好，我先按黑纱记着」只是回声，排在后面。
                        same = 0 if (seq[j].get("role") or "user") == role else 1
                        cand.append((same, d, j))
        cand.sort()
        extra = 0
        for _, _d, j in cand:
            nb = seq[j]
            if nb.get("id") in seen:
                continue
            seen.add(nb.get("id"))
            out.append({
                "id": nb.get("id", ""),
                "ts": nb.get("ts", ""),
                "text": nb.get("text", ""),
                "tags": [],
                "status": "",
                "src": "raw",
                "rel": "neighbor",
                "role": nb.get("role") or "user",
            })
            extra += 1
            if extra >= NEI_MAX:
                break
    return out

# ── 正式库检索 ────────────────────────────────────────────────────

def _rrf_fuse(cands, db, K=60, use_recency=False):
    """四路排名 → RRF 融合。

    cands: [(mem_i, bm25, meta, ts), ...]
    四路：BM25 / 元数据 / 扩散(1 跳，沿共享标签) / 新近度
    常数 K=60 出自 Cormack et al. 2009（RRF 原论文），不是调出来的。
    """
    sc = {}
    def add(rank_list):
        for r, i in enumerate(rank_list[:200]):
            sc[i] = sc.get(i, 0.0) + 1.0 / (K + r)

    # 空的路不许掺和：全零的一路按原顺序排进去，等于给库序加了一个固定偏置。

    # 路1：BM25
    add([i for i, _b, _m, _t in sorted(cands, key=lambda x: -x[1])])
    # 路2：元数据（实体/标签/关键词的字面命中）——全零就不算这一路
    if any(c[2] > 0 for c in cands):
        add([i for i, _b, _m, _t in sorted(cands, key=lambda x: -x[2])])
    # 路3：扩散 —— 从 BM25 前 60 名出发，沿它们挂的标签走一格
    tag2doc = {}
    for i, _b, _m, _t in cands:
        for tg in (db[i].get("small_tags") or []):
            tag2doc.setdefault(str(tg), []).append(i)
    seed = [i for i, _b, _m, _t in sorted(cands, key=lambda x: -x[1])[:60]]
    spread = {}
    for si in seed:
        for tg in (db[si].get("small_tags") or []):
            for nb in tag2doc.get(str(tg), ()):
                if nb != si:
                    spread[nb] = spread.get(nb, 0.0) + 1.0
    add([i for i, _v in sorted(spread.items(), key=lambda x: -x[1])])
    # 路4（新近度）没接：实测无条件加它会把新条目顶上来，真实20题 R@10 18→13。
    # 要用必须先有一个"问句里真的提到时间"的判据，否则不开。见 检索怎么长.md。
    return sc


def search_curated(payload):
    entities = [str(x) for x in (payload.get("entities") or []) if str(x).strip()]
    tags = [str(x) for x in (payload.get("tags") or []) if str(x).strip()]
    query = str(payload.get("query") or "")
    start = norm_time(payload.get("start"))
    end = norm_time(payload.get("end"), is_end=True)
    summary = bool(payload.get("summary"))
    # strict：记忆的 subject / entity / tags 必须在查询里**字面出现**才算命中。
    # 不传实体和标签时默认开启 —— 那是自动召回走的路径，没有归一化，
    # 只能靠这道门把"只共用一个字"的噪音挡在外面。
    cfg = _load_config()
    strict = payload.get("strict")
    if strict is None:
        # 按语气筛是显式条件，不该再被 strict 的门槛挡住
        strict = cfg["strict_when_query_only"] and not (entities or tags or payload.get("tone"))
    limit = cfg["summary_limit"] if summary else cfg["limit"]
    try:
        limit = max(1, min(cfg["max_limit"], int(payload.get("limit") or limit)))
    except Exception:
        pass
    # 宽召回（2026-11-06 夜）：库大到"字面门"每题只放进来 10 条时，门本身成了瓶颈。
    # 打开后，倒排沾到至少一个词的记忆也进池子（打五折排队尾），不再被直接砍掉。
    # 默认按库大小定：小库（自家 136 条）门是可靠的，不需要宽召回；
    # 上千条以后门会把 98% 的候选挡在外面，必须放宽度。
    _wenv = os.environ.get("DSH_MEMORY_WIDE")
    if "wide" in payload:
        wide = bool(payload.get("wide"))
    elif _wenv is not None:
        wide = _wenv == "1"
    else:
        wide = None  # 自动：等读到库再按大小定

    with _Lock():
        db = _read_unlocked()

    want_owner = str(payload.get("owner") or "").strip()
    want_big = str(payload.get("big_tag") or "").strip()
    # 开关：payload 里显式给 off / on，或沿用 config 里持久化的 off_big
    off_big = set(str(x) for x in (payload.get("off") or []) if str(x).strip())
    off_big |= set(str(x) for x in (cfg.get("off_big") or []) if str(x).strip())
    for x in (payload.get("on") or []):
        off_big.discard(str(x).strip())
    # 按语气过滤：问"我生气那次"就只要 stance=生气 的
    want_tone = str(payload.get("tone") or "").strip()
    if want_tone:
        want_tone = TONE_ALIAS.get(want_tone, want_tone)
    scored = []
    # 只靠范围/语气这类显式条件筛，不靠打分决定去留
    time_only = bool((start or end or want_tone) and not entities and not tags and not query)
    explain = bool(payload.get("explain"))
    explain_map = {}
    # 同义词扩展：问句里出现"熬夜"，就把"作息/凌晨/晚睡"整组带进来
    syns = _syn_groups()
    qtext = str(payload.get("query") or "")
    qexp_text = qtext + " " + " ".join(sorted(expand_text(qtext, syns)))
    qexp_tokens = _tokens(qexp_text)
    # 单字问句：只有"重复性不高也不低"的字才让它扫正文（阿归 2026-11-05 提）
    short_ch = _is_short_han(qexp_text)
    short_ok = False
    if short_ch:
        # 上界用"比例"不用绝对数：库长大时自动放宽（2026-11-05 定 40%）
        cap = max(3, int(SHORT_DF_RATIO * len(db)))
        dfv = _char_df(db, short_ch, cap=cap)
        # 封闭类的虚字直接挡——它们在任何语料里都会挤进标签和实体名
        short_ok = (short_ch not in STOP_CHARS) and (1 <= dfv <= cap)

    # ⛳ 硬约束（2026-11-05）：先按"确定知道的东西"把候选池缩一圈，再打分。
    #    确定的东西 = 问句里点名的实体 / 显式给的时间窗。
    #    这一层不改打分，只是把不该进来的挡在外面——他报告里零返回率 46%→0% 靠的就是这个。
    hard_n = 0
    times_only_note = []
    # 时间硬约束：问句里说了"昨天/上周/11月1日"，就只在这个窗里找。
    if bool(payload.get("hard", os.environ.get("DSH_MEMORY_HARD") == "1")) and qtext and not (start or end):
        tw = query_time_window(qtext)
        if tw:
            start, end = tw[0], tw[1]
            why_time = "问句里的时间 → 只找 %s ~ %s" % (start[:10], end[:10])
            if not times_only_note:
                times_only_note.append(why_time)
    if bool(payload.get("hard", os.environ.get("DSH_MEMORY_HARD") == "1")) and qtext:
        named = set()
        for m in db:
            for k in (m.get("entity"), m.get("subject")):
                k = str(k or "")
                if len(k) >= 2 and k in qtext:
                    named.add(k)
        if named:
            keep = []
            for m in db:
                hay = " ".join([str(m.get("entity") or ""), str(m.get("subject") or "")])
                if any(k in hay for k in named) or any(k in str(m.get("text") or "") for k in named):
                    keep.append(m)
            if keep:
                hard_n = len(db) - len(keep)
                db = keep

    # ── 槽位分解（2026-11-06 试）──────────────────────────────
    # 阿归的想法：先把问句拆成「时间 / 专名 / 其余」，再去记忆里分别比。
    # 拆出来的槽位要能拼回去（可逆），所以这里只抽有明确边界的两种：
    #   时间 —— "Today is July 6, 2022" / "2022-07-06"
    #   专名 —— 连续大写的词组（Christmas Superhall / A7C / Peterhouse）
    _SLOTS = os.environ.get("DSH_MEMORY_SLOTS") == "1"
    q_slot_date = None
    q_slot_ents = set()
    if _SLOTS and qtext:
        _MON = {m: i + 1 for i, m in enumerate(
            ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"])}
        mdt = re.search(r"(\d{4})-(\d{2})-(\d{2})", qtext)
        if mdt:
            q_slot_date = (int(mdt.group(1)), int(mdt.group(2)), int(mdt.group(3)))
        else:
            mdt = re.search(r"([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})", qtext)
            if mdt and mdt.group(1).lower() in _MON:
                q_slot_date = (int(mdt.group(3)), _MON[mdt.group(1).lower()], int(mdt.group(2)))
        _STOP_CAP = {"Today", "When", "What", "Where", "Which", "How", "Who", "Why",
                     "The", "This", "That", "Is", "Was", "Did", "Do", "I", "My", "In",
                     "On", "At", "For", "To", "And", "Or", "But", "It", "A", "An"}
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9&\-]{2,}(?:\s+[A-Z][A-Za-z0-9&\-]{2,}){0,2})\b", qtext):
            w = m.group(1)
            if w.split()[0] in _STOP_CAP:
                continue
            q_slot_ents.add(w.lower())

    # 词表候选（倒排）：先算出"每条记忆被问句里的词命中了几次"
    lex_hits = {}
    lex_weight = {}
    lex_bm25 = {}
    _idf_on = os.environ.get("DSH_MEMORY_LEXW", "idf") != "hits"
    _K1, _B = 1.5, 0.75
    # v2 排序（2026-11-06 夜）：把和 BM25 重复计分的那几条老加分关掉。
    # 它们和 BM25 算的是同一件事（词面重合），加两遍等于把噪声也放大两倍。
    _RANK_V2 = os.environ.get("DSH_MEMORY_RANK", "legacy") == "v2"
    # v3 = RRF 四路融合（2026-11-06 夜）。规则见 检索怎么长.md
    _RANK_V3 = os.environ.get("DSH_MEMORY_RANK", "legacy") == "v3"
    _v3 = []
    _cov_w = float(os.environ.get("DSH_MEMORY_COVW", "0") or 0)
    _len_w = float(os.environ.get("DSH_MEMORY_LENW", "0") or 0)
    _dl, avgdl = [], 1.0
    if qtext and len(db) >= 50 and os.environ.get("DSH_MEMORY_NOLEX") != "1":
        idx = _lex_index(db)
        # 查询侧截断：二十一那边是「最多 12 个词，OR 连接」，我们原来取全部。
        # 旋钮默认 0 = 不截断，用来做 A/B。
        _qcap = int(os.environ.get("DSH_MEMORY_QCAP", "0") or 0)
        toks = set(_lex_tokens(qtext, cap=_qcap or 160))
        _N = max(1, len(db))
        _qt_active = 0
        _qt_idf = 0.0
        for t in toks:
            posts = idx.get(t, ())
            if not posts:
                continue
            _qt_active += 1
            _qt_idf += math.log(1.0 + _N / float(len(posts))) if _idf_on else 1.0
            # 词越稀罕越值钱：问"Peterhouse"该比问"the"值钱得多。
            # 只数命中个数的老算法，会把停用词和专名当同一回事。
            _w = math.log(1.0 + _N / float(len(posts))) if _idf_on else 1.0
            # BM25：词频饱和 + 文档长度归一。|C| 大时，这是排序的正路，
            # 之前"每个词 +6 封顶 36"把专名和停用词压成同一个分。
            _idf = math.log(1.0 + (_N - len(posts) + 0.5) / (len(posts) + 0.5))
            _dl_now = _LEX_CACHE.get("dl") or []
            _avg = _LEX_CACHE.get("avgdl") or 1.0
            for pair in posts:
                i, n = pair if isinstance(pair, tuple) else (pair, 1)
                lex_hits[i] = lex_hits.get(i, 0) + 1
                lex_weight[i] = lex_weight.get(i, 0.0) + _w
                _di = _dl_now[i] if i < len(_dl_now) else _avg
                lex_bm25[i] = lex_bm25.get(i, 0.0) + _idf * n * (_K1 + 1) / (
                    n + _K1 * (1.0 - _B + _B * _di / _avg))
        _dl, avgdl = _lex_dl()

    for mem_i, mem in enumerate(db):
        paths = set()
        if want_tone:
            mt = str((mem.get("tone") or {}).get("stance") or "")
            if mt != want_tone:
                continue
        if want_owner:
            mo = str(mem.get("owner") or "")
            if mo and mo != want_owner:
                continue
        if want_big and str(mem.get("big_tag") or "") != want_big:
            continue
        # 大标签开关：关掉的块整块不参与打分。
        # 阿归 2026-11-22 提的：十个大标签本来就是十块，不需要的那块顺手关掉，
        # 别让它来抢位置——"我最近是不是老吃药"被三条架构笔记挤成第四，
        # 那三条全在 AI人设 这块，而这块占库里一半。
        if off_big and str(mem.get("big_tag") or "") in off_big:
            continue
        ts = str(mem.get("timestamp") or "2000-01-01 00:00:00")
        if start and ts < start:
            continue
        if end and ts > end:
            continue

        if _RANK_V3:
            # v3 不走字面门：候选 = 任一信号沾到的条目，谁进前 10 交给 RRF。
            _bm = lex_bm25.get(mem_i, 0.0)
            _mt = 0.0
            me = str(mem.get("entity") or "")
            for ent in entities:
                if not ent:
                    continue
                if ent == me:
                    _mt += 4.0
                elif ent in me:
                    _mt += 2.0
                elif ent in str(mem.get("text") or ""):
                    _mt += 1.0
            mtags = [str(t) for t in (mem.get("small_tags") or [])]
            mkw = [str(t) for t in (mem.get("keywords") or [])]
            for tg in tags:
                if tg in mtags:
                    _mt += 3.0
                elif any(tg in x or x in tg for x in mtags if x):
                    _mt += 1.5
                if tg in mkw:
                    _mt += 1.0
            # 第二条元数据路：条目的 subject/实体/标签/关键词**字面出现在问句里**。
            # 这条不依赖调用方传 entities/tags —— 自动召回走的永远是纯 query +
            # 这条字面路，少了它，中文短问句会整条掉队（实测 R@10 18→14）。
            _keys = [str(mem.get("subject") or ""), str(mem.get("entity") or "")]
            _keys += [str(t) for t in (mem.get("small_tags") or [])]
            _keys += [str(t) for t in (mem.get("keywords") or [])]
            for k in _keys:
                if len(k) < 2:
                    continue
                if k in qexp_text:
                    _mt += 2.0 if len(k) >= 4 else 1.5
                elif len(k) >= 3 and any(k[i:i + 2] in qexp_text for i in range(len(k) - 1)):
                    _mt += 0.8
            tq = str((mem.get("tone") or {}).get("quote") or "")
            if tq and tq in str(payload.get("query") or ""):
                _mt += 2.0
            if _bm > 0 or _mt > 0:
                _v3.append((mem_i, _bm, _mt, ts))
            continue

        # 只给时间窗、一个字都没给的查询（问「11-23 都发生了什么」），
        # 没有 qtext 可匹配，字面门必然全挡死 —— 2026-11-24 实测：
        # {"start":"2026-11-23","end":"...","summary":true} 返回 0 条，
        # 加 strict:false 立刻 10 条。**时间是查询本身，不是缺了查询。**
        if strict and not time_only:
            keys = [str(mem.get("subject") or ""), str(mem.get("entity") or "")] + \
                   [str(t) for t in (mem.get("small_tags") or [])] + \
                   [str(t) for t in (mem.get("keywords") or [])]
            ok = False
            for k in keys:
                if not k:
                    continue
                # 中文一个字就能当信号（"车"），英文词至少两个字符
                cjk = "\u4e00" <= k[0] <= "\u9fff"
                if not cjk:
                    if len(k) >= 2 and k in qtext:
                        ok = True
                        break
                    continue
                # 中文：整个词出现算命中；长词再允许"任意二字片段"命中，
                # 这样问"上海"也能找到 subject="上海出差"。
                if k in qexp_text:
                    ok = True
                    break
                if len(k) >= 3:
                    if any(k[i:i + 2] in qexp_text for i in range(len(k) - 1)):
                        ok = True
                        break
            # 字面门没过，还有一次机会：正文里和问句共享 3 个以上二字片段，
            # 就放进来但打五折。字面门是为了挡噪音，不是为了挡"换个说法"。
            strict_missed = not ok
            # 短问句（一个汉字）走另一条门：整词比不过时，单字沾到也算过。
            # 不改上面那道门——那道门挡的是噪音，这道门只给"问得短"的人用。
            if not ok and _is_short_han(qexp_text):
                ch = _is_short_han(qexp_text)
                # 一刀切：这个字重复性不高不低，才允许它走短问句这条路。
                # 不管它出现在标签上还是正文里 —— 「的/我」这种字本身就不该当检索信号。
                if short_ok and (any(ch in k for k in keys if k) or ch in str(mem.get("text") or "")):
                    ok = True
                    strict_missed = False
            if not ok:
                # 两字问句后门（2026-11-06）：只对"问句本身就是两个汉字"生效，
                # 长问句一步都不走这条路。
                two = _is_short_han2(qexp_text)
                if two and (any(two in k for k in keys if k)
                            or two in str(mem.get("text") or "")):
                    ok = True
                    strict_missed = False
            if not ok:
                hay_txt = str(mem.get("text") or "") + " " + str(mem.get("entity") or "")
                shared_bg = len(_tokens(qexp_text) & _tokens(hay_txt))
                if shared_bg < 3 and _char_overlap(qexp_text, hay_txt) < 3:
                    if wide and lex_hits.get(mem_i, 0) >= 1:
                        strict_missed = True
                    else:
                        continue
        else:
            strict_missed = False

        score = 0
        why = []  # 分数构成：不能解释的打分等于不能调
        for ent in entities:
            me = str(mem.get("entity", ""))
            if not ent:
                continue
            # 完全相等 > 是它的子串 > 只出现在正文里。
            # 不加这一层，"事件2" 会被 "事件20""事件27" 抢走。
            if ent == me:
                score += _RANK_W["entity_exact"]
                paths.add("E")
                why.append("实体完全相等 +%d" % _RANK_W["entity_exact"])
            elif ent in me and _whole_word(me, ent):
                score += _RANK_W["entity_word"]
                paths.add("E")
                why.append("实体是整词子串 +%d" % _RANK_W["entity_word"])
            elif ent in me:
                # 只是「前缀相同」的那种子串，不算命中：
                # 「事件2」不该被「事件20」赚到分。给一点点，别给满。
                score += _RANK_W["entity_prefix"]
                why.append("实体前缀沾边 +%d" % _RANK_W["entity_prefix"])
            elif ent in str(mem.get("text", "")):
                score += _RANK_W["entity_in_text"]
                why.append("实体出现在正文 +%d" % _RANK_W["entity_in_text"])

        m_tags = [str(t) for t in (mem.get("small_tags") or [])]
        for tag in tags:
            for mt in m_tags:
                if tag == mt:
                    score += _RANK_W["tag_exact"]
                    paths.add("T")
                    why.append("标签「%s」完全相等 +30" % tag)
                    break
                if tag and (tag in mt or mt in tag):
                    score += _RANK_W["tag_partial"]
                    paths.add("T")
                    why.append("标签「%s」部分匹配 +15" % tag)
                    break

        # 关键词（2026-11-05）：一次性标签降级后的去处，只给弱分，不抢标签的位置
        m_keywords = [str(k) for k in (mem.get("keywords") or [])]
        for tag in tags:
            for mk in m_keywords:
                if tag == mk:
                    score += _RANK_W["kw_exact"]
                    paths.add("K")
                    why.append("关键词「%s」命中 +8" % mk)
                    break
                if tag and (tag in mk or mk in tag):
                    score += _RANK_W["kw_partial"]
                    why.append("关键词「%s」沾边 +4" % mk)
                    break

        if query and not entities and not tags and not _RANK_V2:
            hay_tokens = _tokens(mem.get("text", "")) | _tokens(" ".join(m_tags)) | _tokens(mem.get("entity", "")) | _tokens(" ".join(m_keywords))
            hit = len(qexp_tokens & hay_tokens)
            if hit:
                _bx = min(10 * hit, _RANK_W["lex_hit_cap"])
                score += _bx
                paths.add("X")
                why.append("分词命中 %d 个 +%d" % (hit, _bx))
            shared = _char_overlap(query, mem.get("text", "") + " " + mem.get("entity", "") + " " + " ".join(m_tags))
            if shared >= 3:
                _bo = min(6 * shared, _RANK_W["char_overlap_cap"])
                score += _bo
                why.append("字面重合 %d 字 +%d" % (shared, _bo))
            # 原句里带着的语气，也是"语境"的一部分：问句里出现那句原话，直接抬上来
            # 短问句（一个汉字）单独给分：这一格以前根本不给分，所以问「药」永远是 0。
            short = _is_short_han(qexp_text)
            if short and short_ok:
                _keys = [str(mem.get("subject") or ""), str(mem.get("entity") or "")] + \
                        [str(t) for t in (mem.get("small_tags") or [])] + \
                        [str(t) for t in (mem.get("keywords") or [])]
                if any(short in k for k in _keys if k):
                    score += _RANK_W["short_han_tag"]
                    why.append("短问句「%s」沾到标签/关键词 +%d" % (short, _RANK_W["short_han_tag"]))
                elif short_ok and short in str(mem.get("text") or ""):
                    score += _RANK_W["short_han_text"]
                    why.append("短问句「%s」在本条正文里（df 不高不低）+%d" % (short, _RANK_W["short_han_text"]))
            tq = str((mem.get("tone") or {}).get("quote") or "")
            if tq and tq in str(payload.get("query") or ""):
                score += _RANK_W["quote_in_query"]
                why.append("问句里出现原句 +%d" % _RANK_W["quote_in_query"])

        # 槽位分：时间对上 / 专名对上，各给一份（2026-11-06 试）
        if _SLOTS:
            if q_slot_date:
                try:
                    _y, _mo, _d = [int(x) for x in ts[:10].split("-")]
                    _gap = abs((_y - q_slot_date[0]) * 365 + (_mo - q_slot_date[1]) * 30 + (_d - q_slot_date[2]))
                except Exception:
                    _gap = 999
                if _gap <= 45:
                    score += 0
                    why.append("槽位·时间接近（差 %d 天）+25" % _gap)
            if q_slot_ents:
                _hay_all = (str(mem.get("text") or "") + " " + str(mem.get("entity") or "")).lower()
                _hit_ents = [e for e in q_slot_ents if e and e in _hay_all]
                if _hit_ents:
                    score += min(60 * len(_hit_ents), 180)
                    why.append("槽位·专名 %d 个命中 +%d" % (len(_hit_ents), min(12 * len(_hit_ents), 36)))

        # 倒排命中：只当"够得着"的通道，分给得低，排序仍靠上面那些硬信号
        _lh = lex_hits.get(mem_i, 0)
        if _lh:
            if _idf_on:
                # 不封顶：一封顶，强候选全挤到同一个分数上，排序就废了。
                # （2026-11-06 实测：封顶 480 时同批题 58%，放开后应回到纯 BM25 的水平）
                _b = int(round(_RANK_W["bm25_scale"] * lex_bm25.get(mem_i, 0.0)))
                # 覆盖率：命中的词占问句里"有索引"的词的比例。问 6 个词中 6 个，比中 1 个强。
                if _cov_w and _qt_idf > 0:
                    # 按"稀罕词覆盖了多少"算，不是按命中个数算：
                    # 中一个 amazon 比中三个 the 值钱。
                    _b += int(round(_cov_w * (lex_weight.get(mem_i, 0.0) / _qt_idf)))
                # 长度归一：同样命中数，长文比短文散，扣一点。
                if _len_w and _dl and avgdl > 0:
                    _dl_i = _dl[mem_i] if mem_i < len(_dl) else avgdl
                    _b += int(round(_len_w * math.log(max(1.0, _dl_i / avgdl))))
                score += _b
                paths.add("L")
                why.append("倒排(idf) 命中 %d 个词 +%d" % (_lh, _b))
            else:
                score += min(6 * _lh, 36)
                paths.add("L")
                why.append("倒排命中 %d 个词 +%d" % (_lh, min(6 * _lh, 36)))

        # 多路径加成（2026-11-05 夜）：被几条不同的路同时命中的，更可能是真的。
        # 这一条是"排序"信号，不加召回——他图遍历能到 31.8% 靠的就是路径计数。
        if len(paths) >= 2 and not _RANK_V2:
            bonus = _RANK_W["multi_path"] * (len(paths) - 1)
            score += bonus
            why.append("多路径 %s +%d" % ("+".join(sorted(paths)), bonus))

        if score > 0 or time_only:
            # 遗忘曲线 R = exp(-ΔT / S)：越老越淡，但原文一直在
            ar = float(mem.get("arousal") or 0.2)
            dcfg = cfg["decay"]
            stab = float(mem.get("stabilityDays") or 0) or (
                float(dcfg["base_days"]) * (1.0 + ar * float(dcfg["arousal_boost"])))
            retention = math.exp(-age_days(ts) / max(stab, 1.0))
            factor = 0.35 + 0.65 * retention
            score = score * factor
            if strict_missed:
                # 宽召回模式下不再打五折：这些条目本来就只能靠倒排进池子，
                # 再砍一半等于把它们永远压在队尾，宽召回就白开了。
                if not wide:
                    score = score * 0.5
                    why.append("字面门没过，按语义近似打五折")
                else:
                    why.append("字面门没过（宽召回，不打折）")
            why.append("遗忘衰减 ×%.2f（%d 天前，arousal %.2f）" % (factor, age_days(ts), ar))
            explain_map[mem.get("id")] = why
            scored.append((score, mem))

    if _RANK_V3:
        _sc = _rrf_fuse(_v3, db, K=_RANK_W.get("rrf_k", 60))
        scored = []
        for i, v in _sc.items():
            mem = db[i]
            ar = float(mem.get("arousal") or 0.2)
            dcfg = cfg["decay"]
            stab = float(mem.get("stabilityDays") or 0) or (
                float(dcfg["base_days"]) * (1.0 + ar * float(dcfg["arousal_boost"])))
            factor = 0.35 + 0.65 * math.exp(-age_days(str(mem.get("timestamp") or "2000-01-01 00:00:00")) / max(stab, 1.0))
            scored.append((v * 100.0 * factor, mem))
            explain_map[mem.get("id")] = ["RRF 融合 %.4f" % v, "衰减 ×%.2f" % factor]

    scored.sort(key=lambda x: (x[0], str(x[1].get("timestamp", ""))), reverse=True)
    picked = scored[:limit]
    # 置信度（元认知那条）：top1 和 top2 差得越远，越敢说"就是这条"。
    # 差得近 = "有货，但线索不够"——这时候该说"我拿不准"，而不是硬答。
    _gap = 0.0
    if len(picked) >= 2 and picked[0][0] > 0:
        _gap = (picked[0][0] - picked[1][0]) / max(1e-9, picked[0][0])
    _conf = "high" if _gap >= 0.25 else ("low" if _gap < 0.08 else "mid")
    # [rankfix-1] 原来这里按时间从老到新重排，把刚算好的分数序抹平了；
    # 取前 limit 之后必须保持分数序，否则"最相关"的那条永远排在最后。

    out = []
    for sc, m in picked:
        item = {
            "id": m.get("id", ""),
            "ts": m.get("timestamp", ""),
            "text": m.get("text", ""),
            "tags": m.get("small_tags", []),
            "status": m.get("status", ""),
            "src": "curated",
            "conf": _conf,
            "kind": m.get("kind") or "event",
            "big_tag": m.get("big_tag") or "",
            "subject": m.get("subject") or m.get("entity") or "",
            "tone": m.get("tone"),
        }
        if explain:
            item["score"] = round(sc, 1)
            item["why"] = explain_map.get(m.get("id")) or []
        out.append(item)
    return out, len(scored)


CONFIG_FILE = os.path.join(os.path.dirname(DB), "config.json")

DEFAULT_CONFIG = {
    "limit": 4,               # 普通检索返回几条
    "summary_limit": 10,      # 汇总模式返回几条
    "max_limit": 20,
    "off_big": [],            # 关掉的大标签（开关）：这些块的记忆检索时不看
    "strict_when_query_only": True,   # 只给 query 时启用标签门
    "decay": {
        "base_days": 30,      # 基准稳定性（天）
        "arousal_boost": 2.0  # 唤起度对稳定性的放大倍数
    },
    "auto": {
        "enabled": True,
        "max": 2,
        "max_chars": 400
    }
}


def _merge(dst, src):
    out = dict(dst)
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_config():
    cfg = DEFAULT_CONFIG
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = _merge(DEFAULT_CONFIG, json.load(f))
        else:
            _ensure_dir()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return cfg


TAG_FILE = os.path.join(os.path.dirname(DB), "tags.json")

# 大标签：固定的分类，不许随便加。小标签挂在它们下面。
SEED_ALIAS = {
    "维修": ["修车", "修理", "修了", "坏了"],
    "搬家": ["搬去", "搬到", "搬家了"],
    "报名": ["报了", "报班", "报名了"],
    "缴费": ["交了", "付了", "交钱"],
    "购买": ["买了", "下单", "入手"],
    "体检": ["查体", "检查身体"],
    "看病": ["去医院", "看医生"],
    "出差": ["出差去", "去外地"],
    "见面": ["碰面", "见了"],
    "加班": ["熬夜", "加班到"],
    "面试": ["面试了", "面了"],
    "戒烟": ["不抽了", "戒了"],
    "健身": ["锻炼", "去健身房"],
    "借款": ["借钱", "借了"],
}

SEED_VOCAB = {
    "资产财务": ["购买", "缴费", "还款", "退货", "工资", "投资"],
    "工作事业": ["面试", "开会", "加班", "汇报", "出差", "辞职"],
    "个人成长": ["报名", "考试", "读书", "练习", "戒烟", "健身"],
    "生活":     ["搬家", "维修", "体检", "做饭", "装修", "借款"],
    "健康":     ["看病", "吃药", "体检", "牙医", "受伤", "睡觉"],
    "社交":     ["见面", "送礼", "聚会", "吵架", "道歉", "借款"],
    "出行":     ["出差", "旅行", "买车", "驾照", "航班", "订票"],
    "AI人设":   ["偏好", "设定", "约定", "承诺"],
}


def _seed_vocab():
    out = {}
    for big, tags in SEED_VOCAB.items():
        alias = {}
        for t in tags:
            if t in SEED_ALIAS:
                alias[t] = list(SEED_ALIAS[t])
        out[big] = {"tags": list(tags), "alias": alias}
    return out


def _load_vocab():
    try:
        with open(TAG_FILE, "r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        vocab = _seed_vocab()
        try:
            _ensure_dir()
            with open(TAG_FILE, "w", encoding="utf-8") as f:
                json.dump(vocab, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
        return vocab


def _save_vocab(v):
    try:
        _ensure_dir()
        tmp = TAG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(v, f, ensure_ascii=False, indent=1)
        os.replace(tmp, TAG_FILE)
    except Exception:
        pass


def resolve_tag(tag, vocab=None, big_tag=None):
    """把一个输入标签归一成词表里的标准词。

    返回 (canonical, how, candidates)：
      how = "exact" | "alias" | "contains" | "new"
    """
    vocab = vocab if vocab is not None else _load_vocab()
    tag = str(tag or "").strip()
    if not tag:
        return "", "empty", []

    pools = {}
    if big_tag and big_tag in vocab:
        pools[big_tag] = vocab[big_tag]
    else:
        pools = vocab

    all_tags, aliases = [], {}
    for big, entry in pools.items():
        for t in entry.get("tags", []):
            all_tags.append((big, t))
        for canon, al in (entry.get("alias") or {}).items():
            for a in al:
                aliases[a] = canon

    if tag in aliases:
        return aliases[tag], "alias", []
    for big, t in all_tags:
        if t == tag:
            return t, "exact", []
    for big, t in all_tags:
        if tag in t or t in tag:
            return t, "contains", []

    # 指定大标签下没找到，就全表再找一次：
    # 否则「报名」在"生活"里会被当成新词，跨大标签重复建词正是碎片化的源头。
    if big_tag:
        for k, entry in vocab.items():
            for t in entry.get("tags", []):
                if t == tag:
                    return t, "other_big", []
            for canon, al in (entry.get("alias") or {}).items():
                if tag in al:
                    return canon, "other_big", []
    # 没命中：给最近的几个候选
    cands = [t for _, t in all_tags if any(ch in t for ch in tag) or any(ch in tag for ch in t)]
    return tag, "new", cands[:3]


HITS_FILE = os.path.join(os.path.dirname(DB), "hits.json")


def _read_hits():
    try:
        with open(HITS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _bump_hits(ids):
    """记录“被召回次数”。不参与打分，只用来找冷记忆。"""
    if not ids:
        return
    hits = _read_hits()
    now = now_str()
    for i in ids:
        rec = hits.get(i) or {"n": 0, "last": ""}
        rec["n"] = int(rec.get("n", 0)) + 1
        rec["last"] = now
        hits[i] = rec
    try:
        _ensure_dir()
        tmp = HITS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hits, f, ensure_ascii=False)
        os.replace(tmp, HITS_FILE)
    except Exception:
        pass


def group_timeline(items, order="time"):
    """按 subject 分世界线：状态类只留“最新一条 + 一条历史”，事件类全留。

    不修改任何存量数据——只在检索这一层排序。旧的永远不被“改写”，
    只是排到后面，需要时还能翻到。

    order 只有两个值，对应**两个不同的层**（2026-11-22 阿归定）：

      · order="time"（默认）—— 线层。给「看这世界怎么发展 / 近况是什么」。
        线内、线之间一律按时间倒序，因为「发展」本身就是一个时间顺序。
        _entity_direct / _recent_line 走这个。

      · order="rank" —— 答案层。给「问到的时候给什么答案」。
        这里**不许**按时间重排：那样会把最相关的那条压到最后（实测问
        「我最近有没有按时吃药」，最新的"巩固"会顶到第一）。这一层只做
        两件事：把同一条线的结果拢在一起、状态类去重，顺序仍按相关度。

    为什么必须分开：同一个东西的不同叫法能合成一条线（线表），但"药"和
    "吃药记录"是**不同概念**，不能进线表；而回答"我最近是不是老吃药"又要
    把它们一起端上来。线层管同一性，答案层管相关性 —— 两层各管各的。
    """
    groups = {}
    for m in items:
        _k = str(m.get("subject") or m.get("entity") or "")
        # 答案层按「线」归拢：同一个东西的不同叫法要挨在一起，不能散开。
        key = _line_of(_k) if order == "rank" else _k
        groups.setdefault(key, []).append(m)

    out = []
    for key, g in groups.items():
        if order == "rank":
            # 保持传进来的相关度顺序。状态类只留 newest(current) + 一条 history。
            _newest = max(g, key=lambda m: str(m.get("ts", "")))
            kind = _newest.get("kind") or "event"
            if kind == "state":
                _newest["rel"] = "current"
                _hist = next((m for m in g if m is not _newest), None)
                if _hist is not None:
                    _hist["rel"] = "history"
                    out.extend([m for m in g if m is _newest or m is _hist])
                else:
                    out.extend(g)
            else:
                out.extend(g)
            continue
        g.sort(key=lambda m: str(m.get("ts", "")), reverse=True)
        kind = g[0].get("kind") or "event"
        if kind == "state" and len(g) > 1:
            g[0]["rel"] = "current"
            g[1]["rel"] = "history"
            out.extend(g[:2])
        else:
            if kind == "state":
                g[0]["rel"] = "current"
            out.extend(g)

    if order == "rank":
        # 组的出场顺序 = 该组第一条的相关度名次；组内不重排。
        return out
    out.sort(key=lambda m: str(m.get("ts", "")), reverse=True)
    return out



# ---- 词表接进写入（2026-11-05 夜）----
# 规矩：自创可以，但要有根。有根 = 整词在标准词表里，或者能拆成标准词。
_WORDS = {"data": None}
_SELF = {"data": None}


def _std_words():
    if _WORDS["data"] is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "词表", "标准词表.json"), encoding="utf-8") as f:
                _WORDS["data"] = set(json.load(f))
        except Exception:
            _WORDS["data"] = set()
    return _WORDS["data"]


def _rooted(tag, depth=3):
    """这个标签有没有根。返回 (有没有根, 拆出来的词)。"""
    t = str(tag or "")
    if not t:
        return (False, [])
    W = _std_words()
    if not W:
        return (True, [t])          # 没有词表就当有根，别误报
    if len(t) >= 2 and t in W:
        return (True, [t])
    # 「检索与标签」这种带连接词的，先切开再各自找根
    parts = [x for x in re.split(r"[与和及]", t) if x]
    if len(parts) > 1:
        got, ok = [], True
        for x in parts:
            r = _rooted(x, depth)
            if not r[0]:
                ok = False
                break
            got += r[1]
        if ok:
            return (True, got)
    n = len(t)
    sys.setrecursionlimit(10000)
    seen = {}

    def go(i, k):
        if i == n:
            return [] if k >= 1 else None
        if k == 0 or (i, k) in seen:
            return seen.get((i, k))
        for j in range(n, i, -1):
            w = t[i:j]
            if len(w) >= 2 and w in W:      # 单字不算根——"吵+相"那种拆法没有意义
                r = go(j, k - 1)
                if r is not None:
                    seen[(i, k)] = [w] + r
                    return seen[(i, k)]
        seen[(i, k)] = None
        return None

    got = go(0, depth)
    if got and len(got) > 1:
        return (True, got)
    return (False, [])


def _selfmade():
    if _SELF["data"] is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "词表", "自创表.json"), encoding="utf-8") as f:
                _SELF["data"] = json.load(f)
        except Exception:
            _SELF["data"] = {}
    return _SELF["data"]


def _record_selfmade(tag):
    """没根的标签，记进自创表（状态=候选），下次再出现就 +1。"""
    d = _selfmade()
    row = d.get(tag)
    if isinstance(row, dict):
        row["用了几次"] = int(row.get("用了几次") or 0) + 1
        if row["用了几次"] >= 3 and row.get("状态") == "候选":
            row["状态"] = "转正"
    else:
        d[tag] = {"根": [], "首见": now_str()[:10], "状态": "候选", "用了几次": 1}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "词表", "自创表.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ── 第 2 跳：标签当路走 ────────────────────────────────────────────

HUB_MIN = 12          # 一个标签挂这么多条以上，就不当边用
HOP2_MAX_TXT = 160    # 第 2 跳的条目只给这么长——它是"提示"，不是"答案"


def _hop2(hit, limit=3):
    """拼图式补全（2026-11-22 重做，阿归三周前的软拼图）。

    老版本顺"小标签"摸 —— 那是网：所有边一样粗，拼上去的东西分不清是原块
    还是近似块，所以他 11-08 留的那条约束（多跳必须逐层传成色）根本没落地。

    这一版只拼**有形状的边**，而且每条边带成色：

      原话 —— 同一条线上的相邻段（同一件事，严丝合缝）
      词串 —— 同一条思路上的前后段（同一串思考，近似但认得出来）

    而且只在**缺块**的时候拼：命中三条以上说明线头已经答上来了，再拼是多余的
    （当年把它做成网，就是从"每次都要跳"开始的）。
    """
    try:
        data = _read_unlocked()
    except Exception:
        return []
    rows = data.get("memories") if isinstance(data, dict) else data
    rows = rows or []
    if not rows:
        return []
    hit = [m for m in (hit or []) if m.get("src") in ("curated", "entity", "short", None, "")]
    if len(hit) >= 3:          # 不缺块，不拼
        return []
    seen = {str(m.get("id") or "") for m in hit if m.get("id")}
    if not seen:
        return []
    by_id = {str(m.get("id") or ""): m for m in rows}

    def _key(m):
        return _line_of(m.get("subject") or m.get("entity") or "")

    # 按线分组，组内按时间排好 —— 相邻段就是拼图的边
    by_line = {}
    for m in rows:
        by_line.setdefault(_key(m), []).append(m)
    for g in by_line.values():
        g.sort(key=lambda x: str(x.get("timestamp") or ""))

    # 思路链的顺序（同一条竖线的前后段）
    _chain = ((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("chain") or {}
    # 思路链要按时间排，不能按我写进 json 的顺序 —— 那个顺序是当时挑的顺序。
    _idea_order = [i for i in ((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("ids") or [] if i]
    _idea_order.sort(key=lambda i: str((by_id.get(i) or {}).get("timestamp") or ""))
    _idea_pos = {i: n for n, i in enumerate(_idea_order)}

    out = []
    for m in hit[:2]:
        mid = str(m.get("id") or "")
        ts = str(m.get("timestamp") or "")
        # ① 原话：同一条线上的前后段
        g = by_line.get(_key(m)) or []
        if g and ts:
            _idx = next((n for n, x in enumerate(g) if str(x.get("id") or "") == mid), None)
            if _idx is not None:
                for off in (-1, 1):
                    j = _idx + off
                    if 0 <= j < len(g):
                        cand = g[j]
                        cid = str(cand.get("id") or "")
                        if cid and cid not in seen:
                            seen.add(cid)
                            it = _as_item(cand, src="hop")
                            it["hop"] = 2
                            it["tone_of_join"] = "原话"
                            it["via"] = "同一条线《%s》的%s" % (_key(m), "前一段" if off < 0 else "后一段")
                            out.append(it)
        # ② 词串：同一条思路上的前后段
        if mid in _idea_pos:
            n = _idea_pos[mid]
            for off in (-1, 1):
                j = n + off
                if 0 <= j < len(_idea_order):
                    cid = _idea_order[j]
                    if cid in seen or cid not in by_id:
                        continue
                    seen.add(cid)
                    it = _as_item(by_id[cid], src="hop")
                    it["hop"] = 2
                    it["tone_of_join"] = "词串"
                    it["via"] = "同一串思考里的%s" % ("前一段" if off < 0 else "后一段")
                    out.append(it)
        if len(out) >= limit:
            break
    return out[:limit]


def _as_item(m, src="entity"):
    """把库里的一条原样转成检索输出格式（不加任何改写）。"""
    return {
        "id": str(m.get("id") or ""),
        "ts": m.get("timestamp", ""),
        "text": m.get("text", ""),
        "tags": m.get("small_tags", []),
        "status": m.get("status", ""),
        "src": src,
        "kind": m.get("kind") or "event",
        "big_tag": m.get("big_tag") or "",
        "subject": m.get("subject") or m.get("entity") or "",
    }


def _entity_direct(payload, limit=4, min_count=3):
    """实体直达：问句里出现库里的高频实体名时，拉出这条「线」。

    2026-11-22，阿归的原话："事件的变化，由时间的变化去区分不同物体的
    变化 —— 就像一条条线。"

    所以这里给的不是"最近 N 条"，是**一条线**：
      · kind=state 的，只取"现在是什么样"(rel=current) + "之前是什么样"
        (rel=history) —— 这两个点就是一次"变化"
      · kind=event 的，是这条线上发生过的事，按时间补在后面
    采样规则直接复用 group_timeline()，不另起一套 —— 线的规则只该有一处。

    min_count 挡掉只出现一两次的实体（噪音，不是"这个人/这件事"）。
    """
    q = str(payload.get("query") or "")
    if len(q) < 2:
        return []
    with _Lock():
        db = _read_unlocked()
    # 按"线"计数，不按实体名计数 —— 六个别名各 3 条，合成一条 18 条的线。
    cnt = {}
    alias_of = {}
    for m in db:
        e = str(m.get("entity") or "").strip()
        if e:
            ln = _line_of(e)
            cnt[ln] = cnt.get(ln, 0) + 1
            alias_of.setdefault(ln, set()).add(e)
    if not cnt:
        return []
    # 问句里出现这条线的任何一个别名，都算点到它
    def _touched(ln):
        names = alias_of.get(ln, set()) | {ln}
        for a in names:
            if not a:
                continue
            if a in q:                      # 问句里出现了这个叫法
                return True
            if len(q) >= 2 and q in a:      # 或者问句本身就是它的简称（问 MC）
                return True
        return False

    hits = [ln for ln, n in cnt.items() if n >= min_count and _touched(ln)]
    if not hits:
        return []
    # 短名字被长名字包含时丢掉短的：问「压缩闸门」，不该把实体「压缩」也拉出来。
    hits.sort(key=lambda e: (-len(e), -cnt[e]))
    _keep = []
    for e in hits:
        if any(e != x and e in x for x in _keep):
            continue
        _keep.append(e)
    hits = _keep
    per = max(2, min(4, limit))
    out, seen = [], set()
    # 挂回去：问的是"这件事当时怎么想的"，就把挂在它上面的思路先端出来。
    # 思路线是竖的，实体线是横的，这两条在这里交叉一次。
    try:
        if _IDEA_RE.search(q):
            _ev_map = ((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("events") or {}
            _ids = [i for i, ev in _ev_map.items() if ev in hits]
            if _ids:
                _by = {str(m.get("id") or ""): m for m in db}
                _rows = [_by[i] for i in _ids if i in _by]
                _items = [_as_item(m) for m in _rows]
                _items.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
                for it in _items[:max(2, per // 2 or 1)]:
                    mid = str(it.get("id") or "")
                    _seen_it = mid in seen
                    if mid:
                        seen.add(mid)
                    if _seen_it:
                        continue
                    it["line"] = "思路"
                    it["event"] = _ev_map.get(mid, "")
                    out.append(it)
    except Exception:
        pass
    for e in hits[:2]:
        rows = [m for m in db if _line_of(m.get("entity")) == e]
        if not rows:
            continue
        items = [_as_item(m) for m in rows]
        items.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
        line = group_timeline(items)
        # 先把"变化"两个点排前面（当前 → 之前），再补这条线上发生的事。
        # 线内就按时间倒序 —— 「发展」本来就是一条时间顺序，不另造优先级。
        # group_timeline 已经把 rel（current / history）打在 state 上了，
        # 模型自己看得出哪个是现在、哪个是从前。
        for it in line[:per]:
            mid = str(it.get("id") or "")
            if mid and mid in seen:
                continue
            seen.add(mid)
            it["line"] = e          # 这条属于哪条线
            out.append(it)
    # 线之间**不做时间混排**：不同的线不相互串联，也就不该相互穿插。
    # 每条线自己是一段，连着给 —— 这才看得出「这个物体怎么发展过来的」。
    return out


# 严一点：光有"最近"不算 —— "我最近是不是老吃药"是在问一件具体的事，
# 不是问近况。要问"整体过得怎么样"才算。
_RECENT_RE = re.compile(
    r"近况|最近.{0,8}(怎么样|如何|过得|还好|在忙|忙什么|在做什么|干啥)"
    r"|这几天.{0,6}(怎么样|如何)|这阵子.{0,6}(怎么样|如何)"
    r"|最近有什么|最近咋样")
_SELF_WORDS = ("我", "咱", "俺")


def _recent_line(payload, limit=4):
    """近况：不问"像不像"，直接给线的头。

    阿归 2026-11-22 的话："这个刚好就可以解决你前面说的那个问题呀，
    想知道我的最近的情况。" —— 对，但卡在一个字上：他问的时候说的是
    "我"，库里那条线叫"阿归"。代词接不上，线再准也拉不出来。

    所以这里只做两件事：认出这是问"近况"，再把第一人称落到人身上。
    落不到人（库里没人名）就退到最高频实体 —— 那是这个库的主线。
    """
    q = str(payload.get("query") or "")
    if len(q) < 2 or not _RECENT_RE.search(q):
        return []
    # 问句里已经有明确的实体名，交给 _entity_direct，不抢
    with _Lock():
        db = _read_unlocked()
    cnt = {}
    for m in db:
        e = str(m.get("entity") or "").strip()
        if e:
            cnt[e] = cnt.get(e, 0) + 1
    if not cnt:
        return []
    # 第一人称：这个库是"我们俩"的库，落到人身上
    # 走到这里说明问句里没有点名任何实体，问的是"我自己"。
    # 这个库里"我"就是阿归；没有他的数据才退到主人。
    target = [e for e in ("阿归", "主人") if cnt.get(e, 0) >= 3][:1]
    if not target:
        return []
    out = []
    for e in target:
        rows = [m for m in db if _line_of(m.get("entity")) == e]
        items = [_as_item(m) for m in rows]
        items.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
        line = group_timeline(items)
        for it in line[:limit]:
            it["line"] = e
            out.append(it)
    return out


# ── 线表（2026-11-22）────────────────────────────────────────────
# 同一个东西的不同叫法 -> 主线名。阿归的原话："吃药和药他们算是一个整体，
# 他们不就有一条线吗？" 对。库里 217 个实体里有 157 个只出现一次，真正散的
# 原因不是数据少，是同一个东西被切成好几条线（我的世界 / MC基岩版世界 /
# 阿归的MC世界 / 渊与阿归的Minecraft世界 …… 六条）。先归一，再串线。
#
# 只改检索时怎么串，不动任何一条记忆 —— 每条的行迹都是真的，不该被改写。
_LINE_CACHE = {"data": None, "key": None}


def _line_map():
    path = os.path.join(os.path.dirname(DB), "线表.json")
    try:
        key = os.path.getmtime(path)
    except OSError:
        return {}
    if _LINE_CACHE["key"] != key:
        try:
            with open(path, encoding="utf-8") as f:
                _d = json.load(f) or {}
            _LINE_CACHE["data"] = _d.get("map") or {}
            _LINE_CACHE["pins"] = _d.get("pins") or {}
        except Exception:
            _LINE_CACHE["data"] = {}
            _LINE_CACHE["pins"] = {}
        _LINE_CACHE["key"] = key
    return _LINE_CACHE["data"] or {}


# ---- 竖线：不挂在实体上，挂在"一个侧面"上（2026-11-22 阿归要的）----
# 他说的是："不同事件的思路 —— 想法、思路、怎么做的，这条线先筛出来，
# 串起来，以后慢慢挂回去。"
#
# 所以它和实体线方向是垂直的：实体线是"一件事从头到尾"，思路线是
# "很多件事里我想的那一下"。先竖着串成一条，以后再各自挂回事件上
# （每条补一个 event 字段就是挂回去了）。
#
# 名单是手挑的，存在 线表.json 的 pins 里 —— 判断"这条是不是思路"
# 需要一个会判断的人，查询时按字面猜只会猜歪（试过一次，拉回来的是
# "看穿"和"不吃"）。
_IDEA_RE = re.compile(r"思路|怎么想|怎么想的|怎么做的|怎么弄的|为什么这么|当初.{0,4}怎么"
                      r"|判断依据|怎么判断|方法是什么|定下什么方案|怎么设计的"
                      r"|决定.{0,4}(做|弄|设计|搞)")


def _pins_of(name):
    _line_map()          # 顺手把缓存刷了
    return ((_LINE_CACHE.get("pins") or {}).get(name) or {}).get("ids") or []


def _idea_line(payload, limit=6):
    """思路线：问"当时怎么想的 / 思路是什么"时，把这条竖线按时间倒序端出来。"""
    q = str(payload.get("query") or "")
    if not q or not _IDEA_RE.search(q):
        return []
    # 两条来源：手挑的（线表 pins）+ 以后写入时自己带的（DB 里的 line 字段）。
    # 只靠手挑会烂第二次 —— 明天新记的又不在线上。
    ids = list(_pins_of("思路"))
    # 挂回去：每条思路都标着它属于哪件事（线表 pins.思路.events）
    _ev = dict(((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("events") or {})
    with _Lock():
        db = _read_unlocked()
    _seen_ids = set(ids)
    for m in db:
        if str(m.get("line") or "") == "思路":
            mid = str(m.get("id") or "")
            if mid and mid not in _seen_ids:
                _seen_ids.add(mid)
                ids.append(mid)
                _ev.setdefault(mid, str(m.get("subject") or m.get("entity") or ""))
    # 短库那条来源：思路是在对话里当场长出来的，长库只留下想成结论的那部分。
    # 所以这条线跟短库一起走 —— 短库每天自动落，我跟在后面把思路挑出来。
    _evs = ((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("evs") or []
    if not ids and not _evs:
        return []
    by_id = {str(m.get("id") or ""): m for m in db}
    rows = [by_id[i] for i in ids if i in by_id]
    if not rows:
        return []
    items = [_as_item(m) for m in rows]
    items.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    n = max(2, min(10, int(limit)))
    out = []
    _chain = ((_LINE_CACHE.get("pins") or {}).get("思路") or {}).get("chain") or {}
    for it in items:
        it["line"] = "思路"
        it["event"] = _ev.get(str(it.get("id") or ""), "")
        _ch = _chain.get(str(it.get("id") or "")) or []
        if len(_ch) >= 2:
            it["from"] = _ch[0]      # 从哪来
            it["to"] = _ch[1]        # 到哪去
        out.append(it)
    # 短库那部分：按事件给，原话不改写。
    # 两边合起来再按时间排 —— 不然长库那 81 条会把新的全挤出去。
    if _evs:
        _want = set(_evs)
        for ev, ts, rs in _duan_events(days=7):
            if ev not in _want:
                continue
            txt = _duan_text(rs)
            if not txt:
                continue
            _ch = _chain.get(ev) or []
            out.append({
                "id": ev, "ts": ts, "text": txt, "tags": [], "status": "",
                "src": "short", "kind": "event", "big_tag": "", "subject": "",
                "line": "思路", "event": _ev.get(ev, ""),
                "from": _ch[0] if len(_ch) >= 2 else "",
                "to": _ch[1] if len(_ch) >= 2 else "",
            })
    out.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    return out[:n]


# ---- 近况档：直接读短库（2026-11-22 接）----------------------------
# 为什么近况不该从长库拿：长库是"我判断完了才写"的库。11-18 到 11-20
# 每天只落 6~8 条，那几天问"最近怎么样"，长库只会给出一个洞。
# 短库不取决于我的判断 —— 同几天每天 250~270 句原话，事件也已经切好
# （每行带 ev="e_20261122_003"）。近况就该拿这个。
_DUAN_SKIP_RECENT = 2
# 最近一次读短库时自检出来的问题。空列表 = 干净。
# 2026-11-22 夜里改：不再用 mtime 做缓存 —— 时间戳能被绕过（cp -p、同秒写），
# 而三天全读只要 39 毫秒。用内容自己说话，比相信文件系统便宜也更准。
_DUAN_CHECK = []


def _duan_dir():
    """短库在哪。beta 目录下没有 短/，往上找一层。"""
    base = os.path.dirname(os.path.abspath(DB))
    for cand in (os.path.join(base, "短"), os.path.join(os.path.dirname(base), "短")):
        if os.path.isdir(cand):
            return cand
    return None


def _duan_days(n=3):
    """最近 n 天的短库文件，按日期倒序。每次都全读（三天约 40ms）。

    顺手做三项自检，结果放 _DUAN_CHECK：
      · 单调 —— 文件名说的日期，要和文件里首末两条说的时间对得上
      · 完整 —— 文件有多少行，就得成功解析多少行
      · 连续 —— 缺掉的那天只记 warn（那天可能本来就没说话）

    出问题不抛异常、不返回空：照常把读得到的给出去，但把缺的写进 _DUAN_CHECK。
    「少一天」必须让人看见，不能变成静默。
    """
    global _DUAN_CHECK
    _DUAN_CHECK = []
    d = _duan_dir()
    if not d:
        _DUAN_CHECK.append("短库目录没找到")
        return []
    try:
        # 只收日期名的文件：短/ 里还有 场景.jsonl（是场景索引，不是那天的原话）
        names = sorted([f for f in os.listdir(d)
                        if re.match(r"^\d{4}-\d{2}-\d{2}\.jsonl$", f)], reverse=True)[:n]
    except Exception as e:
        _DUAN_CHECK.append("短库列目录失败：%s" % type(e).__name__)
        return []
    days = []
    for f in names:
        day_name = f[:10]
        rows, total = [], 0
        try:
            with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    total += 1
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception as e:
            _DUAN_CHECK.append("%s 读不了（%s），这天整个缺" % (day_name, type(e).__name__))
            continue
        if total and len(rows) < total:
            _DUAN_CHECK.append("%s 有 %d 行没解析出来（共 %d 行）"
                               % (day_name, total - len(rows), total))
        if rows:
            first = str(rows[0].get("ts") or "")
            last = str(rows[-1].get("ts") or "")
            if first[:10] != day_name or last[:10] != day_name:
                _DUAN_CHECK.append("%s 的内容日期对不上：首条 %s / 末条 %s"
                                   % (day_name, first[:10] or "?", last[:10] or "?"))
            elif first > last:
                _DUAN_CHECK.append("%s 内部时间倒着走：首条 %s 晚于末条 %s" % (day_name, first, last))
        days.append((day_name, rows))
    if len(days) >= 2:
        try:
            from datetime import date
            ds = [date.fromisoformat(x[0]) for x in days]
            for a, b in zip(ds, ds[1:]):
                gap = (a - b).days - 1
                if gap > 0:
                    _DUAN_CHECK.append("%s 和 %s 之间缺 %d 天（那天可能本来就没说话）"
                                       % (a.isoformat(), b.isoformat(), gap))
        except Exception:
            pass
    return days


def _duan_events(days=3, per_day=40):
    """把短库这几天切成"件"，按这一件最后一句的时间倒序。

    一件 = 同一个 ev。短库切事件时已经按"说话人没换/一来一回/应答"并好了，
    这里不重新切，免得两处规则不一致。
    """
    out = []
    for _day, rows in _duan_days(days):
        groups = {}
        for r in rows:
            ev = str(r.get("ev") or "")
            if not ev:
                continue
            groups.setdefault(ev, []).append(r)
        _one = []
        for ev, rs in groups.items():
            rs.sort(key=lambda x: str(x.get("ts") or ""))
            _one.append((ev, str(rs[-1].get("ts") or ""), rs))
        # 每天各自封顶：以前是全局限 40，结果前几天的事件把后面的天全挤掉了。
        _one.sort(key=lambda x: x[1], reverse=True)
        out.extend(_one[:per_day])
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def _duan_text(rs, cap=360):
    """一件拼成一段：带时间、带角色，太长就截。原话不改写。"""
    buf = []
    for r in rs:
        t = str(r.get("text") or "").replace("\n", " ").strip()
        if not t:
            continue
        who = "阿归" if str(r.get("role")) == "user" else "渊"
        buf.append("%s %s：%s" % (str(r.get("ts") or "")[11:16], who, t))
    s = "\n".join(buf)
    return s[:cap]


def _recent_short(payload, limit=3):
    """近况：不问"像不像"，直接给最近真实发生的那几件。

    只在「问整体近况」时触发 —— 问句里点名了某条线、或者问的是某件具体的
    事（"我最近是不是老吃药"），都轮不到它。

    「这几天」要按天摊开：三件全挤在同一小时里，那不是"这几天怎么样"，
    是"刚才怎么样"。所以每天各取几件，今天那几件还要跳过最前面两件
    —— 那是刚说完的话，上下文里已经有了。

    （2026-11-22 夜里：压缩点那套不进这里。要按任意时间区间挑，用 tools/时窗.py
      —— 那是外面的旋钮，不是焊在读取路径上的暗桩。）
    """
    q = str(payload.get("query") or "")
    if not q or not _RECENT_RE.search(q):
        return []
    # 默认是问"我"；问句里出现了第三方（他/她/汀/汐）就不抢。
    if not any(w in q for w in _SELF_WORDS) and any(w in q for w in ("他", "她", "汀", "汐", "渊")):
        return []
    evs = _duan_events(days=3)
    if not evs:
        # 2026-11-24 补：短库空的时候不能悄悄退回长库就完事 ——
        # 长库那几条是"整理过的旧结论"，看起来还挺像那么回事，
        # 于是"我最近怎么样"会被几条旧事回答，而且一声不响。
        # 阿归那两天问"你是不是记不住事情"，感觉就是这么来的。
        global _DUAN_CHECK
        _DUAN_CHECK.append("短库这几天没有内容（守夜没在跑？）—— 下面是从长库里挑的旧条目，不是最近发生的")
        return []

    days = []
    for _ev, ts, _rs in evs:
        d = ts[:10]
        if d not in days:
            days.append(d)
    n = max(2, min(4, int(limit)))
    per_day = 1 if len(days) >= 3 else (2 if len(days) == 2 else n)

    out, taken = [], {}
    _today = days[0] if days else ""
    _skip = 0
    for ev, ts, rs in evs:
        day = ts[:10]
        if day == _today and _skip < _DUAN_SKIP_RECENT and taken.get(day, 0) == 0:
            _skip += 1
            continue
        if taken.get(day, 0) >= per_day:
            continue
        txt = _duan_text(rs)
        if not txt:
            continue
        taken[day] = taken.get(day, 0) + 1
        out.append({
            "id": ev,
            "ts": ts,
            "text": txt,
            "tags": [],
            "status": "",
            "src": "short",
            "kind": "event",
            "big_tag": "",
            "subject": "",
            "line": "",
        })
        if len(out) >= n:
            break
    return out


def _line_of(e):
    """一个实体归到哪条线。不在表里就是它自己。"""
    e = str(e or "").strip()
    return _line_map().get(e, e)


def recall(payload):
    # 开关只在这里拧一次：search_curated 用它过滤，返回里报同一个结果。
    if not payload.get("off"):
        try:
            _a = _auto_off(payload)
            if _a:
                payload["off"] = _a
        except Exception:
            pass
    scope = str(payload.get("scope") or "auto")
    curated, matched = [], 0
    if scope in ("auto", "curated"):
        curated, matched = search_curated(payload)

    raw = []
    need_raw = False
    if scope == "raw":
        need_raw = True
    elif scope == "auto":
        # 2026-11-24 改：原文层不再当"例外"。
        # 老规矩是「问句带回忆词 且 正式库不足 2 条」才翻原话，实测这道门上得太紧：
        # 开发集 628 题里，原话平均每题只端上来 0.38 条；而原文层单独命中 35.0%，
        # 是三层里最高的一层。把闸放开之后检索 32.8% → 46.7%（明问 +18.5、暗考 +12.6），
        # 答题 A 32.5 → 40.8。成本 0（原文层是本地字面扫，不调模型）。
        # 改成配额制：每次都扫，但要得少（need=6），靠 recall 的 chars 截断控 token。
        # 想退回老行为：DSH_MEMORY_RAW_ALWAYS=0。
        need_raw = os.environ.get("DSH_MEMORY_RAW_ALWAYS", "1") != "0"

    if need_raw:
        raw = search_raw(payload, need=6, explicit=(scope == "raw"))

    # 交接条目：不自动注入，检索时沾边就浮上来，排在前面（那是"此刻的现场"）
    hand = search_handoff(payload) if scope != "raw" else []

    # 实体直达：名字出现就直接端上来，不参与打分竞争。
    ent_direct = []
    _recent_hit = False
    if scope != "raw":
        try:
            ent_direct = _entity_direct(payload, limit=max(1, int(payload.get("limit") or 4)))
        except Exception:
            ent_direct = []
        if not ent_direct:
            _lim = max(2, int(payload.get("limit") or 4))
            # 竖线优先：问"当时怎么想的 / 思路是什么" —— 那是跨很多件事的一条线。
            try:
                ent_direct = _idea_line(payload, limit=min(10, max(4, _lim * 2)))
                _recent_hit = bool(ent_direct)
            except Exception:
                ent_direct = []
            if not ent_direct:
                # 没点名，但问的是"近况"。
                # 先问短库（这几天真实发生了什么），短库没有才退回长库的线头。
                try:
                    ent_direct = _recent_short(payload, limit=min(3, _lim))
                    _recent_hit = bool(ent_direct)
                except Exception:
                    ent_direct = []
            if not ent_direct:
                try:
                    ent_direct = _recent_line(payload, limit=_lim)
                    _recent_hit = bool(ent_direct)
                except Exception:
                    ent_direct = []

    _seen = set(str(m.get("id") or "") for m in hand + ent_direct)
    # 答案层：order="rank" —— 按相关度，不按时间。时间序是「线层」的事。
    _tl = [m for m in group_timeline(curated, order="rank")
           if str(m.get("id") or "") not in _seen]
    # 兜底那部分也挂上它属于哪条线，不然它看着像凭空多出来的一条。
    for _m in _tl:
        _m["line"] = _line_of(_m.get("subject") or _m.get("entity") or "")
    if _recent_hit:
        _tl = []        # 问的是"近况"，别的线不该挤到线头前面去
    memories = hand + ent_direct + _tl + raw

    # 第 2 跳：顺着已命中条目的"小标签"再走一步。
    # 大标签不参与——AI人设 一个就挂 63% 的库，当边用等于把全库连成一团。
    # 枢纽标签（挂 >= HUB_MIN 条）同样跳过。默认关闭，hop=true 才走。
    if payload.get("hop"):
        try:
            extra = _hop2(memories, limit=int(payload.get("hop_limit") or 4))
            memories = memories + extra
        except Exception:
            extra = []

    # 默认不铺全文：一条两千字的原话进了上下文，之后每轮都要重发一遍。
    # 问"当时准确怎么说的"再传 chars=0 取全文。
    try:
        cap = int(payload.get("chars")) if payload.get("chars") is not None else 400
    except (TypeError, ValueError):
        cap = 400
    try:
        off = max(0, int(payload.get("offset") or 0))
    except (TypeError, ValueError):
        off = 0
    # 分段看：一次只取一段。要看后半截就传 offset=400。
    # 理由：工具结果进了历史就每轮重发，一次塞两千字，后面几十轮都在为它付费。
    if cap > 0:
        for m in memories:
            t = str(m.get("text") or "")
            if len(t) > cap:
                piece = t[off:off + cap]
                head = "（第 %d~%d 字 / 共 %d 字）" % (off + 1, off + len(piece), len(t))
                more = ""
                if off + cap < len(t):
                    more = "…要后面就传 offset=%d；要一次看全传 chars=0" % (off + cap)
                m["text"] = (head if off else "") + piece + more
                m["clipped"] = len(t)
    try:
        _bump_hits([m.get("id") for m in memories if m.get("src") == "curated" and m.get("id")])
    except Exception:
        pass
    return {
        "ok": True,
        "total": len(memories),
        "matched": matched + len(raw),
        "returned": len(memories),
        "curated": len(curated),
        "raw": len(raw),
        "truncated": False,
        "off_big": sorted(set(str(x) for x in (_load_config().get("off_big") or [])) |
                          set(str(x) for x in (payload.get("off") or []))),
        # 只在真的用了短库时报：这一层缺了哪一天，别让它静默。
        # 只要体检攒过话就带出去（以前只在有 short 条目时才带，
        # 于是"短库空了"这条恰恰永远带不出去）
        "short_check": list(_DUAN_CHECK),
        "memories": memories,
    }

# ── 写入 / 统计 / 删除 / 归档 ─────────────────────────────────────

def save(payload):
    events = payload.get("events")
    if events is None and payload.get("text"):
        events = [payload]
    events = events or []

    added, skipped, unknown, warnings = [], [], [], []
    with _Lock():
        db = _read_unlocked()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            vocab = _load_vocab()
            big = str(ev.get("big_tag") or "").strip()
            if big not in vocab:
                big = ""
            raw_tags = ev.get("tags") or ev.get("small_tags") or []
            norm = []
            for t in raw_tags:
                canon, how, cands = resolve_tag(t, vocab, big or None)
                if how == "new":
                    unknown.append(t)
                    # 允许模型自己提新词：挂到该大标签下，下次就能复用
                    if big and canon:
                        vocab.setdefault(big, {"tags": [], "alias": {}})
                        if canon not in vocab[big]["tags"]:
                            vocab[big]["tags"].append(canon)
                if canon and canon not in norm:
                    norm.append(canon)
            if unknown and big:
                _save_vocab(vocab)

            item = {
                "id": str(uuid.uuid4())[:8],
                # timestamp = 事情发生的时间（遗忘曲线按它算岁数）；
                # recorded_at = 入档时间，只用来追溯"什么时候记的"
                "timestamp": norm_time(ev.get("timestamp")) or now_str(),
                "recorded_at": now_str(),
                "status": ev.get("status") or "done",
                "big_tag": big,
                "entity": ev.get("entity") or "",
                "small_tags": norm,
                # arousal：0~1 的唤起度，只影响"多难忘"，不参与检索匹配
                "arousal": max(0.0, min(1.0, float(ev.get("arousal") or 0.2))),
                # tone：还原语境和语气用的四件套（姿态/场合/强度/原句）
                "tone": _norm_tone(ev.get("tone")),
                "stabilityDays": float(ev.get("stabilityDays") or 0) or None,
                # 关键词：一次性标签降级后的去处，必须封顶，不然一条记挂十个词就成了噪音源
                "keywords": [str(k) for k in (ev.get("keywords") or []) if str(k).strip()][:6],
                "text": str(ev.get("text") or "").strip(),
                "media_url": ev.get("media_url"),
                # kind: state=会变的状态（住哪、体重、工作），event=发生过的事（修车、报名）
                "kind": ev.get("kind") if ev.get("kind") in ("state", "event") else "event",
                # subject: 世界线的名字。同一 subject 的记忆按时间排成一条时间线。
                "subject": ev.get("subject") or ev.get("entity") or "",
                # owner：多人场景（QQ 群、客服、家庭共享）下这条记忆属于谁。
                # 空 = 全局可见（兼容老数据）。只有检索时显式给 owner 才会过滤。
                "owner": ev.get("owner") or "",
                # line：这条记忆属于哪条线（2026-11-22）。
                # 空 = 不上线。宁可空着，也不要挂错 —— 挂错比不挂更难收拾。
                # 判据只有一条：它能不能写成一句我会再问一次的话。
                "line": str(ev.get("line") or "").strip(),
            }
            if not item["text"]:
                continue

            # ── 入档体检：缺什么当场说，别让洞口留到下次检索 ──
            # 1) 时间：date-only 会被补成 00:00:00，那就是"只到日期"
            if item["timestamp"].endswith(" 00:00:00") and str(ev.get("timestamp") or "").strip():
                warnings.append('「%s」的时间只到日期（%s），没有时分'
                                % (item["text"][:14], item["timestamp"][:10]))
            elif not str(ev.get("timestamp") or "").strip():
                warnings.append('「%s」没给 timestamp，已按入档时间 %s 记下'
                                % (item["text"][:14], item["timestamp"]))
            # 2) 分类与对象：空了就检索不到
            if not big:
                warnings.append('「%s」没给 big_tag（固定几类里挑一个）' % item["text"][:14])
            if not item["entity"]:
                warnings.append('「%s」没给 entity' % item["text"][:14])
            if not item["small_tags"]:
                warnings.append('「%s」没给 tags' % item["text"][:14])
            # 2.5) 有根吗（2026-11-05）：没根的记进自创表，不拦，只报
            for t in item["small_tags"]:
                ok_root, parts = _rooted(t)
                if not ok_root:
                    _record_selfmade(t)
                    warnings.append('「%s」标签「%s」没根（拆不出标准词），已记进自创表' % (item["text"][:14], t))
            if len(item["text"]) < 6:
                warnings.append('「%s」太短，可能记不住事' % item["text"][:14])
            # 3) 情绪：填了就检查词表，别让错词混成方言
            if ev.get("tone"):
                for msg in _tone_ok(ev.get("tone")):
                    warnings.append('「%s」%s' % (item["text"][:14], msg))

            if any(is_duplicate(item, old) for old in db):
                skipped.append(item["text"][:20])
                continue
            db.append(item)
            added.append(item)
        if added:
            _write_unlocked(db)
        total = len(db)

    return {"ok": True, "added": len(added), "skipped": len(skipped), "total": total,
            "ids": [a["id"] for a in added], "unknown_tags": unknown,
            "warnings": warnings}


def homes_of(tag, vocab=None):
    """这个词出现在哪几个大标签下。多个 = 多义词（同词不同义）。"""
    vocab = vocab if vocab is not None else _load_vocab()
    out = []
    for big, entry in vocab.items():
        if tag in (entry.get("tags") or []):
            out.append(big)
    return out


def tags_cmd(payload):
    """词表查询：给一批标签，返回归一结果和候选。写入之前先问它一次。"""
    vocab = _load_vocab()
    big = str(payload.get("big_tag") or "").strip()
    out = []
    for t in (payload.get("tags") or []):
        canon, how, cands = resolve_tag(t, vocab, big or None)
        homes = homes_of(canon, vocab) if canon else []
        out.append({"input": t, "canonical": canon, "how": how,
                    "candidates": cands, "homes": homes, "shared": len(homes) > 1})
    return {"ok": True, "big_tags": list(vocab.keys()), "big_tag": big, "resolved": out,
            "vocab_size": sum(len(v.get("tags", [])) for v in vocab.values())}


# ---- 开关怎么拧：交给渊自己（阿归 2026-11-22 08:03）----------------
# 原则只有一条：**关错了比不关更糟**。关掉一块 = 那块整个答不上来，
# 而这种错很难被发现（我会以为库里没有）。所以默认一动不动，
# 只在问句证据很强的时候关。
#
# 现在只自动关一块：AI人设。因为它占库里一半，而且是唯一一块
# "装的是关于我们的想法"——问吃药、问吃了什么、问天气，都不需要它。
_OFF_HINTS = ("吃", "饭", "药", "睡", "困", "身体", "疼", "牙", "钱", "工资",
              "跑单", "外卖", "天气", "冷", "热", "雨", "回家", "上班", "下班")
_ON_HINTS = ("记忆", "压缩", "检索", "架构", "插件", "版本", "代码", "线表",
             "bench", "跑分", "闸门", "召回", "思路", "库", "档案", "短库")
_AUTO_OFF_BIG = "AI人设"
_SWITCH_LOG = "switch.jsonl"


def _auto_off(payload):
    """这次要不要拧开关。返回要关掉的块列表（通常是空的）。"""
    q = str(payload.get("query") or "")
    if not q:
        return []
    cfg = _load_config()
    if not cfg.get("auto_switch"):
        return []
    if any(h in q for h in _ON_HINTS):
        return []                      # 问的是系统这边，别关
    if not any(h in q for h in _OFF_HINTS):
        return []                      # 没有任何生活侧的强信号，别动
    _log_switch(q, [_AUTO_OFF_BIG], "生活侧信号")
    return [_AUTO_OFF_BIG]


def _log_switch(q, off, why):
    """拧过就留痕。事后能查"我哪次拧错了"，否则这个决定不可检。"""
    try:
        with open(os.path.join(os.path.dirname(DB), _SWITCH_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now_str(), "query": q[:120],
                                "off": off, "why": why}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def switch_cmd(payload):
    """大标签开关（阿归 2026-11-22 的想法）。

    十个大标签本来就是十块。不需要的那块，顺手关掉 —— 它不是被删，只是不参与
    这一次的检索。AI人设 这块占库里一半，问"我最近是不是老吃药"的时候它开着，
    前三条全是架构笔记，药被挤到第四。

    用法：
      {}                                  -> 列出十块和状态
      {"big":"AI人设"}                     -> 关掉
      {"big":"AI人设","on":true}           -> 打开
    """
    cfg = _load_config()
    off = [str(x) for x in (cfg.get("off_big") or [])]
    big = str(payload.get("big") or "").strip()
    if not big:
        with _Lock():
            db = _read_unlocked()
        cnt = {}
        for m in db:
            k = str(m.get("big_tag") or "")
            cnt[k] = cnt.get(k, 0) + 1
        blocks = [{"big": k or "(空)", "rows": v, "on": k not in off}
                  for k, v in sorted(cnt.items(), key=lambda x: -x[1])]
        return {"ok": True, "off": off, "blocks": blocks}
    if payload.get("on"):
        off = [x for x in off if x != big]
    elif big not in off:
        off.append(big)
    r = config_cmd({"set": {"off_big": off}})
    return {"ok": bool(r.get("ok", True)), "off": off, "big": big,
            "state": "on" if big not in off else "off"}


def config_cmd(payload):
    """看 / 改配置。让每个实例自己调成顺手的样子。"""
    cfg = _load_config()
    if payload.get("set"):
        cfg = _merge(cfg, payload["set"])
        try:
            _ensure_dir()
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=1)
            os.replace(tmp, CONFIG_FILE)
        except Exception as e:
            return {"ok": False, "error": "写配置失败: %s" % e}
    return {"ok": True, "path": CONFIG_FILE, "config": cfg}


_TS_FULL = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def dump(_payload):
    """轻量导出：给"补语气标签"这类批处理挑候选用。只读。"""
    with _Lock():
        db = _read_unlocked()
    return {"ok": True, "total": len(db), "items": [{
        "id": m.get("id"),
        "ts": m.get("timestamp"),
        "big": m.get("big_tag") or "",
        "entity": m.get("entity") or "",
        "arousal": m.get("arousal"),
        "tone": m.get("tone"),
        "text": str(m.get("text") or "")[:70],
    } for m in db]}


def context(payload):
    """给一条记忆，捞出它前后几分钟的原话——判断语气要看上下句。

    只看时间窗内的真实记录，不改任何东西。
    """
    win = int(payload.get("window") or 6)  # 分钟
    ts = str(payload.get("ts") or "").strip()
    if not ts and payload.get("id"):
        with _Lock():
            db = _read_unlocked()
        for m in db:
            if m.get("id") == payload.get("id"):
                ts = str(m.get("timestamp") or "")
                break
    t0 = parse_time(ts)
    if not t0:
        return {"ok": False, "error": "需要 ts 或 id"}
    lo = t0 - datetime.timedelta(minutes=win)
    hi = t0 + datetime.timedelta(minutes=win)
    rows = []
    for r in _iter_raw():
        rt = parse_time(r.get("ts"))
        if not rt or rt < lo or rt > hi:
            continue
        rows.append(r)
    rows.sort(key=lambda r: str(r.get("ts") or ""))
    if len(rows) > 16:
        # 只留离目标时间最近的 16 条
        rows.sort(key=lambda r: abs((parse_time(r.get("ts")) - t0).total_seconds()))
        rows = sorted(rows[:16], key=lambda r: str(r.get("ts") or ""))
    return {"ok": True, "ts": ts, "count": len(rows), "rows": [{
        "ts": r.get("ts"),
        "role": r.get("role") or "user",
        "text": str(r.get("text") or "")[:120],
    } for r in rows]}


def retone(payload):
    """补/改一条记忆的语气四件套。只动 tone 字段，别的都不碰。"""
    mid = str(payload.get("id") or "").strip()
    tone = _norm_tone(payload.get("tone"))
    if not mid:
        return {"ok": False, "error": "缺 id"}
    # 先查 id 再验 tone：原来先验 tone，于是「id 写错了」会报成
    # 「tone 不合法」，把人往错的方向带。
    with _Lock():
        db = _read_unlocked()
        hit = None
        for m in db:
            if m.get("id") == mid:
                hit = m
                break
        if hit is None:
            return {"ok": False, "error": "找不到 " + mid}
        if not tone:
            return {"ok": False, "error": "tone 不合法（stance/scene/quote 至少要有一个）"}
        hit["tone"] = tone
        _write_unlocked(db)
    return {"ok": True, "id": mid, "tone": tone}


def audit(_payload):
    """体检：把「只到日期」「缺字段」的条目列出来，供补正。只读。"""
    with _Lock():
        db = _read_unlocked()
    bad_time, bad_field = [], []
    for m in db:
        ts = str(m.get("timestamp") or "")
        if not _TS_FULL.match(ts) or ts.endswith(" 00:00:00"):
            bad_time.append({"id": m.get("id"), "ts": ts,
                             "text": str(m.get("text") or "")[:28]})
        missing = [k for k in ("big_tag", "entity", "small_tags") if not m.get(k)]
        if missing:
            bad_field.append({"id": m.get("id"), "ts": ts,
                              "missing": missing,
                              "text": str(m.get("text") or "")[:28]})
    return {"ok": True, "total": len(db),
            "date_only": bad_time, "missing_fields": bad_field}


def stats(_payload):
    with _Lock():
        db = _read_unlocked()
    by_month = {}
    for m in db:
        key = str(m.get("timestamp", ""))[:7]
        by_month[key] = by_month.get(key, 0) + 1
    recent = sorted(db, key=lambda m: str(m.get("timestamp", "")), reverse=True)[:3]

    raw_files, raw_bytes = [], 0
    for p in _raw_files():
        try:
            raw_bytes += os.path.getsize(p)
            raw_files.append(os.path.basename(p))
        except Exception:
            pass

    hits = _read_hits()
    cold = [m for m in db if not hits.get(str(m.get("id")))]
    hot_pairs = [((hits.get(str(m.get("id"))) or {}).get("n", 0), m) for m in db
                 if hits.get(str(m.get("id")))]
    hot = sorted(hot_pairs, key=lambda x: x[0], reverse=True)[:3]

    suggestions = []
    if len(db) >= 20 and len(cold) / max(len(db), 1) > 0.6:
        suggestions.append("六成以上记忆从没被召回：可能标签太偏，或 strict 门太严")
    no_big = [m for m in db if not m.get("big_tag")]
    if len(db) >= 10 and len(no_big) / len(db) > 0.5:
        suggestions.append("过半记忆没有大标签：分类是空的，检索只能靠小标签")
    if raw_bytes > 20 * 1024 * 1024:
        suggestions.append("原始库超过 20MB：可以 memory_archive 压一下")
    if not suggestions:
        suggestions.append("暂无明显问题")

    return {
        "ok": True,
        "total": len(db),
        "cold": len(cold),
        "config": _load_config(),
        "suggestions": suggestions,
        "hot": [{"id": m.get("id"), "n": n, "preview": str(m.get("text", ""))[:30]} for n, m in hot],
        "db": DB,
        "raw_dir": RAW_DIR,
        "raw_files": raw_files,
        "raw_bytes": raw_bytes,
        "by_month": by_month,
        "recent": [{"id": m.get("id"), "ts": m.get("timestamp"), "preview": str(m.get("text", ""))[:40]}
                   for m in recent],
    }


def forget(payload):
    mid = str(payload.get("id") or "").strip()
    if not mid:
        return {"ok": False, "error": "缺少 id"}
    with _Lock():
        db = _read_unlocked()
        left = [m for m in db if str(m.get("id")) != mid]
        if len(left) == len(db):
            return {"ok": False, "error": "正式库里没有这条 id（原始库不能单条删，按月份归档）"}
        _write_unlocked(left)
    return {"ok": True, "removed": 1, "total": len(left)}


def archive(payload):
    days = int(payload.get("days") or ARCHIVE_AFTER_DAYS)
    cutoff = (_now().replace(tzinfo=None) - datetime.timedelta(days=days)).strftime("%Y-%m")
    done = []
    for p in _raw_files():
        if p.endswith(".gz"):
            continue
        if os.path.basename(p)[:7] >= cutoff:
            continue
        try:
            with open(p, "rb") as src, gzip.open(p + ".gz", "wb") as dst:
                dst.write(src.read())
            os.remove(p)
            done.append(os.path.basename(p) + " -> .gz")
        except Exception as e:
            done.append(os.path.basename(p) + " 失败: " + str(e))
    return {"ok": True, "archived": done, "cutoff": cutoff}


PAUSE_FILE = os.path.join(os.path.dirname(DB), "PAUSE")


def _paused():
    """有 PAUSE 文件 = 全局静默：不读、不写、不召回。

    这样“开一个完全没有记忆的对话”不用改配置、不用重启 ——
    新建一个 PAUSE 文件即可，删掉就恢复。
    """
    try:
        return os.path.exists(PAUSE_FILE)
    except Exception:
        return False


def migrate(payload):
    """显式合并旧库。{"from": ["/path/a.json"], "dry": true}

    只加不删不改：按 id 和正文去重；原库一个字不动；合并前先把目标库备份。
    升级后看到 notice 里有「旧库未合并」，就跑这个。
    """
    srcs = payload.get("from") or payload.get("sources") or []
    if isinstance(srcs, str):
        srcs = [srcs]
    if not srcs:
        srcs = [q for q in _OLD_DB_PATHS
                if q and os.path.abspath(q) != os.path.abspath(DB)]
    expanded = []
    for q in srcs:
        if not q:
            continue
        if any(c in q for c in "*?["):
            expanded.extend(sorted(glob.glob(q)))
        else:
            expanded.append(q)
    # 去重：显式给的通配和默认列表可能扫到同一份，重复列出来只是噪音
    _seen, _uniq = set(), []
    for q in expanded:
        if not q or not os.path.exists(q):
            continue
        rp = os.path.realpath(q)
        if rp in _seen:
            continue
        _seen.add(rp)
        _uniq.append(q)
    srcs = _uniq
    if not srcs:
        return {"ok": False, "error": "没找到可合并的旧库（用 {\"from\": [路径]} 指定）"}

    target = _read_unlocked()
    before = len(target)
    have_ids = {str(m.get("id")) for m in target if m.get("id")}
    have_text = {_flat(m.get("text") or "") for m in target}
    report, add = [], []
    for q in srcs:
        try:
            with open(q, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as e:
            report.append({"from": q, "error": "%s: %s" % (type(e).__name__, e)})
            continue
        items = d.get("memories") if isinstance(d, dict) else d
        if not isinstance(items, list):
            report.append({"from": q, "error": "不是库文件"})
            continue
        added = skipped = 0
        for m in items:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or "")
            mtx = _flat(m.get("text") or "")
            if (mid and mid in have_ids) or (mtx and mtx in have_text):
                skipped += 1
                continue
            add.append(m)
            if mid:
                have_ids.add(mid)
            if mtx:
                have_text.add(mtx)
            added += 1
        report.append({"from": q, "条数": len(items), "并入": added, "重复跳过": skipped})

    if payload.get("dry"):
        return {"ok": True, "dry": True, "before": before,
                "would_add": len(add), "sources": report}
    if add:
        if os.path.exists(DB):
            shutil.copy2(DB, DB + ".bak-before-migrate." +
                         datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        _write_unlocked(target + add)
        for q in srcs:
            _migrate_siblings(q)
            _mark_migrated(q)      # 合并过就记下，别每轮再喊
    return {"ok": True, "before": before, "after": before + len(add),
            "added": len(add), "sources": report, "note": "原库未动，只追加"}


def lines_check(payload):
    """线的体检：建一条线要守的规矩，做成能跑的东西，不然它只是一句话。

    四条规矩（2026-11-22 定）：
      ① 先有货再建线：≥3 条，且跨 ≥2 天（一天里 3 条可能只是一件事的片段）
      ② 能写成一句问句：写不出来的不建（"阿归"问不出来，"阿归穿什么"可以）
      ③ 先查重：同一个东西加别名，不新建
      ④ 建了能退：删线表一行即可，条目上的 line 留着不删

    体检只报告，不自动改 —— 线是手写的表，改它也该是手。
    """
    with _Lock():
        db = _read_unlocked()
    path = os.path.join(os.path.dirname(DB), "线表.json")
    try:
        with open(path, encoding="utf-8") as f:
            tbl = json.load(f) or {}
    except Exception:
        tbl = {}
    mp = tbl.get("map") or {}
    pins = tbl.get("pins") or {}
    rows = {}
    for m in db:
        ln = _line_of(m.get("entity"))
        rows.setdefault(ln, []).append(m)
    # 正式线 = 别名指向的主线 + 声明式的线（没有别名，自己就是名字）
    official = sorted(set(mp.values()) | set(tbl.get("lines") or []))
    out, warn = [], []
    for ln in official:
        rs = rows.get(ln, [])
        days = len({str(m.get("timestamp") or "")[:10] for m in rs})
        item = {"line": ln, "rows": len(rs), "days": days,
                "aliases": sum(1 for k, v in mp.items() if v == ln)}
        if len(rs) < 3:
            warn.append("%s：只有 %d 条，按规矩该降成候选" % (ln, len(rs)))
        elif days < 2:
            warn.append("%s：%d 条全在 %d 天里，可能只是一件事的几个片段" % (ln, len(rs), days))
        out.append(item)
    # 重复：一条线的名字是另一条的父串
    for i, a in enumerate(official):
        for b in official[i + 1:]:
            if a and b and (a in b or b in a):
                warn.append("%s / %s：名字互为父子串，先确认是不是同一个东西" % (a, b))
    # 候选：>=3 条却还没上线的实体
    refuse = tbl.get("refuse") or {}
    cand = []
    for e, rs in rows.items():
        if not e or e in official or e in refuse:
            continue
        days = len({str(m.get("timestamp") or "")[:10] for m in rs})
        if len(rs) >= 3:
            cand.append({"entity": e, "rows": len(rs), "days": days})
    cand.sort(key=lambda x: -x["rows"])
    return {"ok": True, "official": out,
            "idea": {"ids": len(pins.get("思路", {}).get("ids") or []),
                     "evs": len(pins.get("思路", {}).get("evs") or [])},
            "refused": [{"entity": k, "why": v} for k, v in refuse.items()],
            "candidates": cand[:15], "warnings": warn,
            "rules": "①≥3条且跨≥2天 ②能写成一句问句 ③先查重再加别名 ④建了能退"}


COMMANDS = {"capture": capture, "save": save, "recall": recall, "tags": tags_cmd,
            "config": config_cmd, "handoff": handoff, "miss": miss, "persona": persona, "triage": triage,
            "stats": stats, "audit": audit, "migrate": migrate, "dump": dump, "context": context, "retone": retone, "forget": forget, "archive": archive, "lines": lines_check, "switch": switch_cmd}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd not in COMMANDS:
        print(json.dumps({"ok": False, "error": "未知命令: " + cmd}, ensure_ascii=False))
        return 2
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        print(json.dumps({"ok": False, "error": "入参不是合法 JSON: %s" % e}, ensure_ascii=False))
        return 2
    if _paused() and cmd in ("capture", "save", "recall"):
        # 静默返回空，不给模型任何“记忆存在”的暗示
        empty = {"ok": True, "paused": True}
        if cmd == "recall":
            empty.update({"total": 0, "matched": 0, "returned": 0,
                          "curated": 0, "raw": 0, "memories": []})
        else:
            empty.update({"added": 0, "skipped": 0, "captured": 0, "total": 0})
        print(json.dumps(empty, ensure_ascii=False))
        return 0

    try:
        result = COMMANDS[cmd](payload)
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 1
    if _NOTICE and isinstance(result, dict):
        result.setdefault("notice", _NOTICE)   # 迁移/空库提示必须让模型看见
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
