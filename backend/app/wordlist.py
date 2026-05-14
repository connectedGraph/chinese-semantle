"""
谜底候选词白名单加载器（已废弃，保留作向后兼容）。

> 注意：该模块已不再被运行期使用。词表加载逻辑现在统一收敛于
> `app.engine.WordVectorEngine._load_target_pool()`，因为它需要与
> embedding 在同一进程内做"是否在词向量中"的二次过滤。

`target_words.txt` 由 `scripts/build_wordlist.py` 离线生成，
首行为 `# generated at ...` 注释，其余每行一词。
启动时一次性读入内存，仅保留词本身用于随机抽样。
若文件不存在，返回 None，调用方应回落到全词表行为。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WORDLIST_FILE = "target_words.txt"


def load_target_pool() -> Optional[List[str]]:
    """加载高频词白名单。文件缺失时返回 None。"""
    path = DATA_DIR / WORDLIST_FILE
    if not path.exists():
        logger.warning(
            "Wordlist file not found: %s. Target words will fall back to "
            "full vocabulary. Run scripts/build_wordlist.py to generate it.",
            path,
        )
        return None

    words: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 允许纯词或 "词\t频\t词性" 两种格式
            word = line.split("\t", 1)[0]
            if word:
                words.append(word)

    logger.info("Loaded target pool: %d words from %s", len(words), path)
    return words
