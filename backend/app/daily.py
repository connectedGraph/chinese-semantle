"""
每日挑战（Daily Challenge）模块。

职责
----
1. 提供 CN 时区（UTC+8）下的「今日」日期，作为发题分界
2. 加载运营人员维护的 YAML 配置文件 `backend/data/daily_challenges.yaml`
3. 为未配置的日期，按 HMAC-SHA256(SECRET, "daily:YYYY-MM-DD") 在
   `backend/data/daily_pool.txt`（缺失时回落 `target_words.txt`）中
   稳定抽取一个谜底
4. 提供「日期范围 → [{date, has_config, is_published, ...}]」的日历查询

设计要点
--------
- 不引入 client_id / 反作弊：每日挑战从此刻起一律不计分（详见 plan.md）
- 配置文件只读：启动时一次性加载，改动需重启服务；笔误（不在 target_words 里）
  仅打告警，不 fail boot —— 该日运行时降级到 HMAC 兜底
- HMAC 不可反推：复用 PUZZLE_SECRET，玩家无法预测未来题目

环境变量
--------
- PUZZLE_SECRET：与 puzzle_codes 共用密钥（生产必填）
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---- 时区：CN（UTC+8） ----

CN_TZ = timezone(timedelta(hours=8), name="CN")

# ---- 起始 / 配置路径 ----

DAILY_LAUNCH_DATE = date(2026, 5, 14)
"""可回溯的最早日期（含）。早于此的日期日历上不展示，也不允许 ?daily= 参数访问。"""

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SCHEDULE_FILE = DATA_DIR / "daily_challenges.yaml"
DAILY_POOL_FILE = DATA_DIR / "daily_pool.txt"
TARGET_WORDS_FILE = DATA_DIR / "target_words.txt"

# 与 puzzle_codes 共用 SECRET，避免运营再维护一份密钥
_DEFAULT_SECRET = "spacekid-semantle-dev-secret-do-not-use-in-prod"


def _get_secret() -> bytes:
    return os.environ.get("PUZZLE_SECRET", _DEFAULT_SECRET).encode("utf-8")


# ---- CN 时区工具 ----

def cn_now() -> datetime:
    """当前 CN 时间。"""
    return datetime.now(CN_TZ)


def cn_today() -> date:
    """当前 CN 时区下的自然日。"""
    return cn_now().date()


def parse_date(s: str) -> Optional[date]:
    """解析 YYYY-MM-DD；失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def is_published(d: date, today: Optional[date] = None) -> bool:
    """
    日期是否已发布（可挑战）：
    - DAILY_LAUNCH_DATE ≤ d ≤ today（CN 时区）
    """
    today = today or cn_today()
    return DAILY_LAUNCH_DATE <= d <= today


# ---- 日期池加载 ----

def _load_word_list(path: Path) -> List[str]:
    """读一份纯文本词表（每行一个，# 注释，跳过空行），保持原有顺序但**去重**。"""
    if not path.exists():
        return []
    seen: Set[str] = set()
    out: List[str] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # 兼容形如 "词 1234" 的行（与 build_wordlist 产物一致）
                w = line.split("\t", 1)[0].split()[0].strip()
                if not w or w in seen:
                    continue
                seen.add(w)
                out.append(w)
    except OSError as e:
        logger.error("Failed to read %s: %s", path, e)
        return []
    return out


_pool_cache: Optional[List[str]] = None
_target_set_cache: Optional[Set[str]] = None


def _get_daily_pool() -> List[str]:
    """
    返回 daily_pool.txt 的词列表（已去重）。
    文件不存在或为空时回落到 target_words.txt 并打告警。
    缓存到进程结束。
    """
    global _pool_cache
    if _pool_cache is not None:
        return _pool_cache

    pool = _load_word_list(DAILY_POOL_FILE)
    if not pool:
        logger.warning(
            "daily_pool.txt missing or empty (%s); falling back to target_words.txt",
            DAILY_POOL_FILE,
        )
        pool = _load_word_list(TARGET_WORDS_FILE)
    if not pool:
        logger.error(
            "Both daily_pool.txt and target_words.txt are empty/missing; "
            "daily challenges will be unavailable."
        )
    # **稳定排序**保证「不同部署 / 不同启动顺序」算出同一个 HMAC 抽签结果
    pool = sorted(pool)
    _pool_cache = pool
    logger.info("Daily pool loaded: %d words", len(pool))
    return pool


def _get_target_set() -> Set[str]:
    """target_words.txt 的全集，用于校验 schedule.yaml 配置词是否合法。"""
    global _target_set_cache
    if _target_set_cache is not None:
        return _target_set_cache
    words = _load_word_list(TARGET_WORDS_FILE)
    _target_set_cache = set(words)
    return _target_set_cache


# ---- YAML 配置加载 ----

@dataclass(frozen=True)
class _ScheduleEntry:
    date: date
    word: str


_schedule_cache: Optional[Dict[date, str]] = None


def _load_schedule() -> Dict[date, str]:
    """
    加载 daily_challenges.yaml。
    格式：
        schedule:
          "2026-05-14": 米饭
          "2026-05-15": 山水

    校验：
    - 跳过非法日期 / 非字符串
    - 配置词必须存在于 target_words.txt（否则告警 + 该日丢弃）
    """
    if not SCHEDULE_FILE.exists():
        logger.info("daily_challenges.yaml not found at %s; using HMAC-only mode.", SCHEDULE_FILE)
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        logger.error(
            "PyYAML is not installed but daily_challenges.yaml exists. "
            "Install it via `pip install pyyaml`. Falling back to HMAC-only."
        )
        return {}
    try:
        with SCHEDULE_FILE.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.error("Failed to parse %s: %s", SCHEDULE_FILE, e)
        return {}

    raw = data.get("schedule") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        logger.warning("daily_challenges.yaml has no `schedule` mapping; using HMAC-only.")
        return {}

    out: Dict[date, str] = {}
    targets = _get_target_set()
    for k, v in raw.items():
        # PyYAML 可能把 "2026-05-14" 解析成 date 对象（视引号情况）
        if isinstance(k, date):
            d = k
        else:
            d = parse_date(str(k))
        if d is None:
            logger.warning("daily_challenges.yaml: skip bad date key %r", k)
            continue
        if not isinstance(v, str) or not v.strip():
            logger.warning("daily_challenges.yaml: skip empty/non-str value for %s", d)
            continue
        word = v.strip()
        if targets and word not in targets:
            logger.warning(
                "daily_challenges.yaml: %s -> %r 不在 target_words.txt 中，"
                "本日将降级到 HMAC 兜底",
                d, word,
            )
            continue
        out[d] = word
    logger.info("Daily schedule loaded: %d entries", len(out))
    return out


def _get_schedule() -> Dict[date, str]:
    global _schedule_cache
    if _schedule_cache is None:
        _schedule_cache = _load_schedule()
    return _schedule_cache


def reload_schedule() -> int:
    """强制重新加载配置文件（供测试 / 运维使用）。返回条目数。"""
    global _schedule_cache, _pool_cache, _target_set_cache
    _schedule_cache = None
    _pool_cache = None
    _target_set_cache = None
    return len(_get_schedule())


# ---- HMAC 兜底 ----

def _hmac_pick(d: date, pool: List[str]) -> Optional[str]:
    """对日期 HMAC 抽签：稳定、不可反推。"""
    if not pool:
        return None
    msg = f"daily:{d.isoformat()}".encode("utf-8")
    digest = hmac.new(_get_secret(), msg, sha256).digest()
    idx = int.from_bytes(digest[:8], "big") % len(pool)
    return pool[idx]


# ---- 对外接口 ----

def target_for(d: date) -> Optional[str]:
    """
    返回指定日期的谜底词。
    优先级：YAML 配置 > HMAC(SECRET, daily:YYYY-MM-DD) on daily_pool.txt
    日期未发布 / 池为空时返回 None（调用方应抛 4xx）。
    """
    if not is_published(d):
        return None
    schedule = _get_schedule()
    if d in schedule:
        return schedule[d]
    pool = _get_daily_pool()
    return _hmac_pick(d, pool)


def has_config(d: date) -> bool:
    """该日是否由运营手动配置（仅用于日志 / 调试，前端不暴露）。"""
    return d in _get_schedule()


@dataclass(frozen=True)
class CalendarItem:
    date: date
    is_published: bool
    has_config: bool


def calendar_range(
    start: Optional[date] = None,
    end: Optional[date] = None,
    today: Optional[date] = None,
) -> List[CalendarItem]:
    """
    返回 [start, end] 区间内每一天的元信息。
    缺省取「当月 1 号 → 今天 + 14 天」。

    设计取舍：**返回完整请求区间，不在后端裁剪**。日历视图需要把早于
    DAILY_LAUNCH_DATE 的日期和未来日期也展示出来（仅以「未发布」禁用态呈现），
    避免前端出现「空白月份」的体验问题。`is_published` 字段直接告知前端
    每一天是否可挑战。
    """
    today = today or cn_today()
    if start is None:
        start = today.replace(day=1)
    if end is None:
        end = today + timedelta(days=14)
    if start > end:
        return []

    schedule = _get_schedule()
    out: List[CalendarItem] = []
    cur = start
    while cur <= end:
        out.append(CalendarItem(
            date=cur,
            is_published=is_published(cur, today=today),
            has_config=cur in schedule,
        ))
        cur += timedelta(days=1)
    return out


__all__ = [
    "CN_TZ",
    "DAILY_LAUNCH_DATE",
    "cn_now",
    "cn_today",
    "parse_date",
    "is_published",
    "target_for",
    "has_config",
    "reload_schedule",
    "calendar_range",
    "CalendarItem",
]
