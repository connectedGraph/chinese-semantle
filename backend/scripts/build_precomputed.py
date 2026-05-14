"""
离线预计算所有谜底词的 Top-K 邻居 + 反向编号表。

产物
----
1. backend/data/precomputed/neighbors.sqlite
     - puzzles(code, target, target_len, neighbors=gzip(json))
     - neighbor_index(code, word, sim, rank)
2. backend/data/precomputed/puzzle_codes.json  -- code → word 反向表

运行
----
    cd backend
    python -m scripts.build_precomputed
    # 或
    python -m scripts.build_precomputed --top-k 1000 --rebuild

依赖
----
- 需要本地有词向量文件（backend/data/embedding.kv 或 light_Tencent_AILab_ChineseEmbedding.bin）
- 需要谜底白名单 backend/data/target_words.txt（由 scripts/build_wordlist.py 生成）

耗时
----
- ~1781 词 × Top-1000 计算 ≈ 5-15 分钟（取决于机器）
- 生成的 SQLite 体积约 50-80MB（gzip 后的邻居 blob 累积）
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import List

# 把 backend/ 加入 sys.path，让脚本能通过 `python -m scripts.build_precomputed` 运行
THIS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = THIS_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.engine import get_engine  # noqa: E402
from app.engine_light import NEIGHBORS_DB, PRECOMPUTED_DIR  # noqa: E402
from app.puzzle_codes import (  # noqa: E402
    REVERSE_MAP_FILE,
    build_reverse_map,
    save_reverse_map,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("build_precomputed")

TARGET_WORDS_FILE = BACKEND_DIR / "data" / "target_words.txt"


def _load_target_words() -> List[str]:
    """读取 backend/data/target_words.txt，兼容 build_wordlist.py 输出格式。"""
    if not TARGET_WORDS_FILE.exists():
        raise FileNotFoundError(
            f"{TARGET_WORDS_FILE} not found. "
            "Run scripts/build_wordlist.py first."
        )
    words: List[str] = []
    with TARGET_WORDS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            w = line.split("\t", 1)[0].split()[0].strip()
            if w:
                words.append(w)
    return words


def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS puzzles (
            code TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            target_len INTEGER NOT NULL,
            neighbors BLOB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_puzzles_target ON puzzles(target);

        CREATE TABLE IF NOT EXISTS neighbor_index (
            code TEXT NOT NULL,
            word TEXT NOT NULL,
            sim REAL NOT NULL,
            rank INTEGER NOT NULL,
            PRIMARY KEY (code, word)
        );
        CREATE INDEX IF NOT EXISTS idx_neighbor_word ON neighbor_index(word);
        """
    )
    return conn


def build(top_k: int, rebuild: bool) -> None:
    t_start = time.time()
    words = _load_target_words()
    logger.info("Loaded %d target words from %s", len(words), TARGET_WORDS_FILE)

    # 1. 生成反向编号表（先做冲突检测，失败就别浪费后面 10 分钟）
    logger.info("Building reverse code map …")
    rev = build_reverse_map(words)
    save_reverse_map(rev, REVERSE_MAP_FILE)
    word_to_code = {w: c for c, w in rev.items()}

    # 2. 准备数据库
    if rebuild and NEIGHBORS_DB.exists():
        logger.warning("Removing existing DB: %s", NEIGHBORS_DB)
        NEIGHBORS_DB.unlink()
    conn = _init_db(NEIGHBORS_DB)

    # 3. 加载词向量引擎（本地有 ~116MB 的轻量腾讯词向量）
    logger.info("Loading word vector engine (this takes a few seconds) …")
    engine = get_engine()
    logger.info("Engine ready. Computing Top-%d neighbors for each puzzle …", top_k)

    # 4. 逐个谜底预计算
    existing = {
        row[0] for row in conn.execute("SELECT code FROM puzzles").fetchall()
    }
    to_build = [w for w in words if word_to_code[w] not in existing]
    logger.info(
        "Total: %d puzzles | Already done: %d | To build: %d",
        len(words), len(existing), len(to_build),
    )

    t_loop = time.time()
    for i, word in enumerate(to_build, 1):
        code = word_to_code[word]
        neighbors = engine.top_k(word, k=top_k)
        if not neighbors:
            logger.warning("Skip %r: no neighbors returned from engine.", word)
            continue

        payload = gzip.compress(
            json.dumps(neighbors, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            compresslevel=6,
        )

        conn.execute(
            "INSERT OR REPLACE INTO puzzles(code, target, target_len, neighbors) "
            "VALUES (?, ?, ?, ?)",
            (code, word, len(word), payload),
        )
        # 同步写邻居索引：用于 O(1) 查询某词在某谜底中的相似度 / rank
        conn.executemany(
            "INSERT OR REPLACE INTO neighbor_index(code, word, sim, rank) VALUES (?, ?, ?, ?)",
            [(code, w, float(s), idx + 1) for idx, (w, s) in enumerate(neighbors)],
        )

        if i % 20 == 0 or i == len(to_build):
            conn.commit()
            elapsed = time.time() - t_loop
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(to_build) - i) / rate if rate > 0 else 0
            logger.info(
                "[%d/%d] %r → %s (%.1f puz/s, ETA %.0fs)",
                i, len(to_build), word, code, rate, eta,
            )

    conn.commit()
    conn.close()

    # 5. 体积报告
    size_mb = NEIGHBORS_DB.stat().st_size / 1024 / 1024
    code_size_kb = REVERSE_MAP_FILE.stat().st_size / 1024
    elapsed = time.time() - t_start
    logger.info(
        "Done in %.1fs. neighbors.sqlite=%.1fMB, puzzle_codes.json=%.1fKB",
        elapsed, size_mb, code_size_kb,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Precompute Top-K neighbors for all puzzles.")
    ap.add_argument("--top-k", type=int, default=1000, help="Top-K邻居数量（默认 1000）")
    ap.add_argument("--rebuild", action="store_true", help="清空已有 DB，从头重建")
    args = ap.parse_args()
    build(top_k=args.top_k, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
