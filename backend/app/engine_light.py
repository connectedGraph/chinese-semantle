"""
LightEngine —— 不加载词向量的轻量引擎，仅基于离线预计算的 Top-K 邻居表工作。

设计
----
预计算数据存储在 backend/data/precomputed/neighbors.sqlite：
  table puzzles(
    code TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    target_len INTEGER NOT NULL,
    neighbors BLOB NOT NULL  -- gzip(json.dumps([[word, sim], ...]))
  )
  table neighbor_index(
    code TEXT,
    word TEXT,
    sim REAL,
    rank INTEGER,
    PRIMARY KEY (code, word)
  ) -- 用于 O(1) similarity / rank 查询

为什么用 SQLite？
- Python 内置，零依赖
- 单文件，Vercel 部署友好（一个文件而不是 1781 个）
- 多进程安全
- 查询 O(log n)，比读完整 JSON 快

LightEngine 接口语义
--------------------
- 不同于 LocalEngine 的"全词表":LightEngine 的 vocab 是「所有谜底的 Top-1000 邻居并集」
- has(word)：词是否在 *某个* 谜底的 Top-1000 内
- similarity(target, word)：仅当 (target, word) 在邻居表中才返回真实相似度；否则返回 -1.0
- top_k(target, k)：仅对预计算过的谜底有效；对未知词抛 ValueError
- random_word：从 puzzles.target 中随机抽
- is_common：固定 False（高频池由 LocalEngine 维护，离线构建时已在选词阶段过滤）

调用方注意：game.py 创建谜底时只会从 random_word() 返回的"已预计算谜底"中选，
所以 top_k 永远不会被打到"未知词"。猜词时 similarity 走的是 sim_map（O(1) 命中或 -1），
也不需要打到原始词向量。
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from .puzzle_codes import encode_word

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRECOMPUTED_DIR = DATA_DIR / "precomputed"
NEIGHBORS_DB = PRECOMPUTED_DIR / "neighbors.sqlite"


class LightEngine:
    """轻量版引擎：基于预计算邻居表，运行时不加载词向量。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # 缓存所有可作为谜底的词表（小，全量加载到内存）
        self._target_words: List[str] = self._load_target_words()
        # 缓存所有在某个邻居表里出现过的词（用于 has() 检查）
        self._known_words: set = self._load_known_words()
        logger.info(
            "LightEngine ready: %d puzzles, %d known words in neighbor union.",
            len(self._target_words), len(self._known_words),
        )

    # ---- 装载 ----

    @classmethod
    def load(cls, db_path: Optional[Path] = None) -> "LightEngine":
        path = db_path or NEIGHBORS_DB
        if not path.exists():
            raise FileNotFoundError(
                f"Precomputed neighbors database not found: {path}.\n"
                f"Run `python -m scripts.build_precomputed` to generate it."
            )
        return cls(path)

    def _load_target_words(self) -> List[str]:
        cur = self._conn.execute("SELECT target FROM puzzles")
        return [r["target"] for r in cur]

    def _load_known_words(self) -> set:
        """加载所有 neighbor_index 中的词，用于 has() 检查。"""
        cur = self._conn.execute("SELECT DISTINCT word FROM neighbor_index")
        return {r["word"] for r in cur}

    # ---- EngineProtocol 实现 ----

    def has(self, word: str) -> bool:
        """词是否在任意一个谜底的 Top-1000 邻居中（或者本身是某个谜底）。"""
        if not word:
            return False
        return word in self._known_words or word in self._target_words

    def is_common(self, word: str) -> bool:
        # 高频池信息在离线构建时已用于"选词"阶段，运行时无需再判断；
        # 保持返回 False，让 game.py 走通用路径。
        return False

    @property
    def has_target_pool(self) -> bool:
        return False  # 同上

    def similarity(self, w1: str, w2: str) -> float:
        """
        在预计算表中查 (w1, w2) 的相似度。
        - w1 是谜底，w2 是猜词 → O(log n) 查 neighbor_index
        - 不在邻居表 → 返回 -1.0（语义同 LocalEngine 找不到词时）
        """
        if w1 == w2:
            return 1.0
        code = encode_word(w1)
        cur = self._conn.execute(
            "SELECT sim FROM neighbor_index WHERE code = ? AND word = ?",
            (code, w2),
        )
        row = cur.fetchone()
        if row is None:
            return -1.0
        return float(row["sim"])

    def top_k(self, word: str, k: int = 1000) -> List[Tuple[str, float]]:
        """读取 word 这个谜底的 Top-K 邻居（按 sim 降序）。"""
        code = encode_word(word)
        cur = self._conn.execute(
            "SELECT neighbors FROM puzzles WHERE code = ?",
            (code,),
        )
        row = cur.fetchone()
        if row is None:
            # 词不是预计算过的谜底
            return []
        # 解压
        try:
            payload = gzip.decompress(row["neighbors"])
            neighbors: List[List] = json.loads(payload)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Bad neighbors blob for code=%s: %s", code, e)
            return []
        return [(w, float(s)) for w, s in neighbors[:k]]

    def random_word(
        self,
        min_len: int = 2,
        max_len: int = 4,
        candidates: Optional[List[str]] = None,
    ) -> str:
        pool = candidates or self._target_words
        if not pool:
            raise RuntimeError("LightEngine has no target words.")
        # 简单按长度过滤；目标词表本来已经是 2-4 字常用词，循环很容易命中
        for _ in range(50):
            w = random.choice(pool)
            if min_len <= len(w) <= max_len:
                return w
        return random.choice(pool)

    @property
    def vocab_size(self) -> int:
        return len(self._known_words)


__all__ = ["LightEngine", "NEIGHBORS_DB"]
