"""
引擎抽象基类与运行时选择器。

为什么有这个文件？
--------------------
项目支持两种运行模式：
1. **LocalEngine**（本地开发）：基于 gensim KeyedVectors，加载 ~116MB 词向量；
   能实时计算任意两词相似度、任意词的 Top-K 邻居。
2. **LightEngine**（Vercel 部署）：完全不加载词向量，只读「预计算邻居表」
   （由 scripts/build_precomputed.py 离线生成），运行时内存 <100MB，冷启动 <1 秒。

两种引擎共享同一接口契约（本文件的 EngineProtocol），上层 game.py / API 路由
完全无感。具体实现见 engine.py（Local）和 engine_light.py（Light）。

环境变量
--------
- SEMANTLE_ENGINE：可选值
    - "local"：强制走 LocalEngine（默认；本地推荐）
    - "light"：强制走 LightEngine（Vercel 部署推荐）
    - "auto"（默认）：先尝试 LightEngine（如果预计算文件存在），否则回落 LocalEngine
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class EngineProtocol(Protocol):
    """所有引擎实现需要满足的最小契约。"""

    # ---- 基础查询 ----
    def has(self, word: str) -> bool: ...
    def is_common(self, word: str) -> bool: ...
    @property
    def has_target_pool(self) -> bool: ...

    # ---- 语义查询 ----
    def similarity(self, w1: str, w2: str) -> float: ...
    def top_k(self, word: str, k: int = 1000) -> List[Tuple[str, float]]: ...

    # ---- 选词 ----
    def random_word(
        self,
        min_len: int = 2,
        max_len: int = 4,
        candidates: Optional[List[str]] = None,
    ) -> str: ...

    @property
    def vocab_size(self) -> int: ...


# ---- 运行时选择 ----

_cached_engine: Optional[EngineProtocol] = None


def get_runtime_engine() -> EngineProtocol:
    """
    根据 SEMANTLE_ENGINE 环境变量返回引擎实例。单例。

    选择顺序：
      1. 显式指定 "light" → LightEngine（缺数据会抛 FileNotFoundError）
      2. 显式指定 "local" → LocalEngine（缺数据会回落到 MockEngine）
      3. "auto"（默认）：优先 LightEngine（预计算文件存在），否则 LocalEngine
    """
    global _cached_engine
    if _cached_engine is not None:
        return _cached_engine

    mode = (os.environ.get("SEMANTLE_ENGINE") or "auto").lower().strip()
    logger.info("Engine mode: %s", mode)

    if mode == "light":
        _cached_engine = _make_light_engine()
        return _cached_engine

    if mode == "local":
        _cached_engine = _make_local_engine()
        return _cached_engine

    # auto: 优先 light，失败回落 local
    try:
        _cached_engine = _make_light_engine()
        logger.info("Auto mode picked LightEngine.")
    except FileNotFoundError as e:
        logger.warning("LightEngine unavailable (%s). Falling back to LocalEngine.", e)
        _cached_engine = _make_local_engine()
    return _cached_engine


def _make_light_engine() -> EngineProtocol:
    from .engine_light import LightEngine
    return LightEngine.load()


def _make_local_engine() -> EngineProtocol:
    from .engine import get_engine_or_mock
    return get_engine_or_mock()


def reset_engine_cache() -> None:
    """仅供测试使用。"""
    global _cached_engine
    _cached_engine = None


__all__ = ["EngineProtocol", "get_runtime_engine", "reset_engine_cache"]
