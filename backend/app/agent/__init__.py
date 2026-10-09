"""Agent 对战模块：DeepSeek 工具调用 + 并行猜词。"""
from __future__ import annotations

from typing import Optional

from .deepseek import DeepSeekClient  # noqa: F401
from .race import RaceManager

_manager: Optional[RaceManager] = None


def init_manager(store) -> RaceManager:
    global _manager
    _manager = RaceManager(store)
    return _manager


def get_manager() -> RaceManager:
    if _manager is None:
        raise RuntimeError("RaceManager 未初始化（应在 lifespan 中调用 init_manager）")
    return _manager


__all__ = ["DeepSeekClient", "RaceManager", "init_manager", "get_manager"]
