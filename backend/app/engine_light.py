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

为什么不再用 neighbor_index 行索引表？
- 1781 谜底 × 1000 邻居 = 178 万行索引 + 索引树，sqlite 体积膨胀到 ~150MB
- GitHub 单文件 100MB 限制，超了 push 不上去
- 改为：blob 是唯一真源，per-puzzle 启动时懒解压到 dict 缓存
- 1 个谜底的 dict 约 50KB，1781 个全装内存 ≈ 90MB；按需懒装更省

为什么用 SQLite？
- Python 内置，零依赖
- 单文件，Vercel 部署友好（一个文件而不是 1781 个）
- 多进程安全
- per-puzzle blob 查询 O(log n)

LightEngine 接口语义
--------------------
- 不同于 LocalEngine 的"全词表":LightEngine 的 vocab 是「所有谜底的 Top-1000 邻居并集」
- has(word)：词是否在 *某个* 谜底的 Top-1000 内
- similarity(target, word)：仅当 (target, word) 在 target 的邻居表中才返回真实相似度；否则返回 -1.0
- top_k(target, k)：仅对预计算过的谜底有效；对未知词返回 []
- random_word：从 puzzles.target 中随机抽
- is_common：固定 False（高频池由 LocalEngine 维护，离线构建时已在选词阶段过滤）
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .puzzle_codes import encode_word

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRECOMPUTED_DIR = DATA_DIR / "precomputed"
NEIGHBORS_DB = PRECOMPUTED_DIR / "neighbors.sqlite"


class LightEngine:
    """轻量版引擎：基于预计算邻居表，运行时不加载词向量。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

        # ----- 部署诊断（Vercel 等只读 fs / 文件被截断的情况）-----
        try:
            st = db_path.stat()
            size = st.st_size
            is_symlink = db_path.is_symlink()
            head_bytes = b""
            try:
                with open(db_path, "rb") as f:
                    head_bytes = f.read(16)
            except Exception as e:  # noqa: BLE001
                head_bytes = f"<read failed: {e}>".encode()
            logger.info(
                "LightEngine db diagnostic: path=%s, size=%d bytes, "
                "is_symlink=%s, head=%r",
                db_path, size, is_symlink, head_bytes,
            )
            if not head_bytes.startswith(b"SQLite format 3"):
                raise RuntimeError(
                    f"Database file at {db_path} is not a valid SQLite file. "
                    f"size={size}, head={head_bytes!r}."
                )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"DB stat failed: {db_path} does not exist at runtime."
            )

        # 用 immutable=1 强制只读，避免在只读 fs 上尝试创建 -journal/-wal 文件
        uri = f"file:{db_path}?mode=ro&immutable=1"
        logger.info("Connecting to sqlite uri=%s", uri)
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        # 验证 schema
        try:
            tables = [
                r[0]
                for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            logger.info("LightEngine: sqlite tables = %s", tables)
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                f"Failed initial sqlite query on {db_path}: {e}."
            ) from e

        # ---- 内存缓存 ----
        # per-puzzle 邻居字典：code -> {word: sim}（懒装载）
        self._neighbors_cache: Dict[str, Dict[str, float]] = {}
        # per-puzzle 邻居有序列表：code -> [(word, sim), ...] 按 sim 降序（懒装载）
        self._neighbors_list_cache: Dict[str, List[Tuple[str, float]]] = {}

        # 缓存所有可作为谜底的词表
        self._target_words: List[str] = self._load_target_words()
        # 缓存所有邻居词的并集（用于 has() 检查）
        # 一次性把所有 blob 都解压一遍，构建全局已知词集
        self._known_words: set = self._load_known_words()
        logger.info(
            "LightEngine ready: %d puzzles, %d known words in neighbor union, "
            "%d puzzles cached.",
            len(self._target_words), len(self._known_words),
            len(self._neighbors_cache),
        )

    # ---- 装载 ----

    @classmethod
    def load(cls, db_path: Optional[Path] = None) -> "LightEngine":
        path = db_path or NEIGHBORS_DB
        if not path.exists():
            parent_listing: List[str] = []
            data_listing: List[str] = []
            try:
                if PRECOMPUTED_DIR.exists():
                    parent_listing = sorted(p.name for p in PRECOMPUTED_DIR.iterdir())
                if DATA_DIR.exists():
                    data_listing = sorted(p.name for p in DATA_DIR.iterdir())
            except Exception:  # noqa: BLE001
                pass
            raise FileNotFoundError(
                f"Precomputed neighbors database not found: {path}\n"
                f"  PRECOMPUTED_DIR exists={PRECOMPUTED_DIR.exists()}, "
                f"contents={parent_listing}\n"
                f"  DATA_DIR exists={DATA_DIR.exists()}, contents={data_listing}\n"
                f"  Run `python -m scripts.build_precomputed` to generate it."
            )
        return cls(path)

    def _load_target_words(self) -> List[str]:
        cur = self._conn.execute("SELECT target FROM puzzles")
        return [r["target"] for r in cur]

    def _load_known_words(self) -> set:
        """启动时扫描所有 blob，构建全局已知词集。同时缓存解压后的邻居字典。

        注意：这一步把所有 1781 个 blob 解压并装内存。
        实测 ~80MB 内存占用、~3 秒耗时（lambda cold start 友好）。
        """
        known: set = set()
        cur = self._conn.execute("SELECT code, neighbors FROM puzzles")
        for row in cur:
            code = row["code"]
            try:
                payload = gzip.decompress(row["neighbors"])
                neighbors: List[List] = json.loads(payload)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Bad neighbors blob for code=%s: %s", code, e)
                continue
            # 同时填充 per-puzzle 缓存（避免 query 时再解压一次）
            sim_map: Dict[str, float] = {}
            ordered: List[Tuple[str, float]] = []
            for w, s in neighbors:
                fs = float(s)
                sim_map[w] = fs
                ordered.append((w, fs))
                known.add(w)
            self._neighbors_cache[code] = sim_map
            self._neighbors_list_cache[code] = ordered
        return known

    # ---- EngineProtocol 实现 ----

    def has(self, word: str) -> bool:
        """词是否在任意一个谜底的 Top-1000 邻居中（或者本身是某个谜底）。"""
        if not word:
            return False
        return word in self._known_words or word in self._target_words

    def is_common(self, word: str) -> bool:
        # 高频池信息在离线构建时已用于"选词"阶段，运行时无需再判断
        return False

    @property
    def has_target_pool(self) -> bool:
        return False

    def similarity(self, w1: str, w2: str) -> float:
        """
        查 (w1, w2) 的相似度。
        - w1 是谜底，w2 是猜词 → 查内存 dict
        - 不在邻居表 → 返回 -1.0（语义同 LocalEngine 找不到词时）
        """
        if w1 == w2:
            return 1.0
        code = encode_word(w1)
        sim_map = self._neighbors_cache.get(code)
        if sim_map is None:
            return -1.0
        return sim_map.get(w2, -1.0)

    def top_k(self, word: str, k: int = 1000) -> List[Tuple[str, float]]:
        """读取 word 这个谜底的 Top-K 邻居（按 sim 降序）。"""
        code = encode_word(word)
        ordered = self._neighbors_list_cache.get(code)
        if ordered is None:
            return []
        return ordered[:k]

    def random_word(
        self,
        min_len: int = 2,
        max_len: int = 4,
        candidates: Optional[List[str]] = None,
    ) -> str:
        pool = candidates or self._target_words
        if not pool:
            raise RuntimeError("LightEngine has no target words.")
        for _ in range(50):
            w = random.choice(pool)
            if min_len <= len(w) <= max_len:
                return w
        return random.choice(pool)

    @property
    def vocab_size(self) -> int:
        return len(self._known_words)


__all__ = ["LightEngine", "NEIGHBORS_DB"]

