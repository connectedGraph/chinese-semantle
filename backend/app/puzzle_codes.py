"""
谜底编号（puzzle_code）系统。

设计目标
--------
1. 不可反推：玩家拿到编号无法还原谜底词（依赖服务端密钥 PUZZLE_SECRET）。
2. 稳定：同一个词永远对应同一个编号；白名单增删不影响已有编号。
3. 与词表顺序无关：不是 #1/#2 自增 ID，纯粹由词本身派生。

实现
----
- 正向（word → code）：HMAC-SHA256(SECRET, word) → 截 30 bit → base32 → 6 字符
- 反向（code → word）：构建期生成 reverse map（dict），运行期 O(1) 查询
  - 这个 reverse map 体积很小（1781 词 × ~20 字节 ≈ 35KB），可以直接打包进函数
  - 冲突处理：6 字符 base32 = 2^30 ≈ 10 亿空间，1781 词碰撞概率 < 1e-6，理论可忽略；
    若真的命中冲突，构建期会 raise，需要换 SECRET 或截 7 字符。

环境变量
--------
- PUZZLE_SECRET：服务端密钥。未设置时使用一个内置 default 值（仅本地开发友好；
  生产部署前请通过 Vercel 环境变量覆盖，以确保编号不可被外部破解）。
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---- 配置 ----

PUZZLE_CODE_LEN = 6  # base32 字符数（每字符 5 bit → 总 30 bit 空间 = ~1e9）
# 默认密钥仅用于本地开发；生产部署务必通过 PUZZLE_SECRET 环境变量覆盖
_DEFAULT_SECRET = "spacekid-semantle-dev-secret-do-not-use-in-prod"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REVERSE_MAP_FILE = DATA_DIR / "precomputed" / "puzzle_codes.json"


def _get_secret() -> bytes:
    return os.environ.get("PUZZLE_SECRET", _DEFAULT_SECRET).encode("utf-8")


# ---- 正向：word → code ----

def encode_word(word: str, secret: Optional[bytes] = None) -> str:
    """
    对单个谜底词计算 puzzle_code。

    步骤：
      digest = HMAC-SHA256(SECRET, word)
      截前 30 bit (= 6 个 base32 字符)
      用 base32（去 padding，大写）输出

    示例：encode_word("米饭") -> "K3F8M2"（取决于 SECRET）
    """
    key = secret if secret is not None else _get_secret()
    digest = hmac.new(key, word.encode("utf-8"), sha256).digest()
    # 取前 4 字节（32 bit），base32 编码后取前 6 字符 = 30 bit
    b32 = base64.b32encode(digest[:4]).decode("ascii").rstrip("=")
    return b32[:PUZZLE_CODE_LEN]


# ---- 反向：code → word ----
#
# 反向表在运行期是只读的；模块导入时懒加载一次，全进程共享。

_reverse_map_cache: Optional[Dict[str, str]] = None


def _load_reverse_map() -> Dict[str, str]:
    """
    加载 code → word 反向表。优先从离线产物读取；缺失时尝试从本地 LocalEngine
    的目标池实时构建（保证未跑 build_precomputed 的本地 dev 也能 code↔word）。
    """
    if REVERSE_MAP_FILE.exists():
        try:
            with REVERSE_MAP_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                logger.info("Loaded puzzle_code reverse map: %d entries", len(data))
                return data
            logger.error("Bad reverse map format in %s", REVERSE_MAP_FILE)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to load reverse map %s: %s", REVERSE_MAP_FILE, e)

    # Fallback: 直接从 target_words.txt 现场计算反向表
    # 这条路径仅在「未跑 build_precomputed」的本地 dev 环境出现；生产 / Vercel
    # 一定走文件路径（更快、不依赖 LocalEngine）
    fallback = _build_reverse_map_from_target_words()
    if fallback:
        logger.warning(
            "Reverse map not found on disk; built fallback from target_words.txt "
            "(%d entries). Run `python -m scripts.build_precomputed` to persist it.",
            len(fallback),
        )
        return fallback

    logger.warning(
        "Reverse map not found: %s and target_words.txt also missing. "
        "code→word lookup will return None until you run build_precomputed.",
        REVERSE_MAP_FILE,
    )
    return {}


def _build_reverse_map_from_target_words() -> Dict[str, str]:
    """从 backend/data/target_words.txt 现场计算反向表（不依赖词向量引擎）。"""
    tw = DATA_DIR / "target_words.txt"
    if not tw.exists():
        return {}
    rev: Dict[str, str] = {}
    try:
        with tw.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                w = line.split("\t", 1)[0].split()[0].strip()
                if not w:
                    continue
                code = encode_word(w)
                # 冲突时第一个胜出（与 build_reverse_map 抛错策略不同：
                # 这是软 fallback，能用就行）
                rev.setdefault(code, w)
    except OSError:
        return {}
    return rev


def decode_code(code: str) -> Optional[str]:
    """
    通过 puzzle_code 反查谜底词。
    - code 不区分大小写，会标准化为大写
    - 未找到返回 None
    """
    global _reverse_map_cache
    if _reverse_map_cache is None:
        _reverse_map_cache = _load_reverse_map()
    return _reverse_map_cache.get(code.upper())


def reload_reverse_map() -> int:
    """强制重新加载反向表。返回条目数。供测试 / 热更新使用。"""
    global _reverse_map_cache
    _reverse_map_cache = _load_reverse_map()
    return len(_reverse_map_cache)


# ---- 构建工具：批量生成 + 冲突检测 ----

def build_reverse_map(words: List[str], secret: Optional[bytes] = None) -> Dict[str, str]:
    """
    给一份词表批量生成 code → word 反向表。
    冲突检测：若两个词撞到同一个 code，抛出 ValueError。
    """
    rev: Dict[str, str] = {}
    for w in words:
        code = encode_word(w, secret=secret)
        if code in rev and rev[code] != w:
            raise ValueError(
                f"puzzle_code collision: {rev[code]!r} and {w!r} both → {code!r}. "
                f"Consider increasing PUZZLE_CODE_LEN or changing PUZZLE_SECRET."
            )
        rev[code] = w
    return rev


def save_reverse_map(rev: Dict[str, str], path: Optional[Path] = None) -> Path:
    """把反向表写到磁盘（JSON）。"""
    out = path or REVERSE_MAP_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(rev, f, ensure_ascii=False, separators=(",", ":"))
    logger.info("Saved reverse map: %d entries → %s", len(rev), out)
    return out


__all__ = [
    "PUZZLE_CODE_LEN",
    "encode_word",
    "decode_code",
    "reload_reverse_map",
    "build_reverse_map",
    "save_reverse_map",
]
