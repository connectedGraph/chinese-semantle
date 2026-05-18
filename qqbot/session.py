"""会话状态管理：群/私聊维度持有当前活跃 game。

设计要点：
- 一个 conversation（群或私聊用户）= 一局活跃游戏
- 内存缓存 puzzle_code + history + 标志位，用于后端 404 时透明重建
- 兼容多线程并发猜词：每个 session 自带 asyncio.Lock 串行化对同 game 的写操作
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GameSession:
    """一局活跃游戏。conversation_id = group_openid 或 c2c openid。"""

    conversation_id: str
    game_id: str
    puzzle_code: str
    target_length: int
    source: str  # 'random' | 'daily' | 'shared'
    is_scoring: bool = False
    hint_ever_used: bool = False
    give_up_ever: bool = False
    is_finished: bool = False
    daily_date: Optional[str] = None

    # 缓存的 history（GuessRecord dict），用于：
    #   1. 后端 404 时按 puzzle_code 重建 + 重放
    #   2. 同 puzzle 内同词去重（不区分玩家）
    history: list[dict] = field(default_factory=list)
    # 已猜词集合（小写化以避免大小写歧义；中文 lower() 不变）
    guessed_words: set[str] = field(default_factory=set)
    # 终局答案（is_finished 后才会有值）
    target_word: Optional[str] = None
    # 创建时间，用于后续过期清理
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    # 串行化对该 session 的并发写操作
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_active = time.time()

    def append_history(self, record: dict) -> None:
        self.history.append(record)
        word = (record.get("word") or "").strip()
        if word:
            self.guessed_words.add(word)

    def find_history(self, word: str) -> Optional[dict]:
        """同词复用：返回缓存中已存在的最新 record（None 表示没猜过）。"""
        if word not in self.guessed_words:
            return None
        # 反向遍历找最新一条（极少数情况下可能有同词多条，理论上 set 命中即有）
        for rec in reversed(self.history):
            if rec.get("word") == word:
                return rec
        return None

    def replace_game(
        self, *, game_id: str, history: list[dict]
    ) -> None:
        """后端重建后调用：替换 game_id 与权威 history。"""
        self.game_id = game_id
        self.history = list(history)
        self.guessed_words = {
            (r.get("word") or "").strip()
            for r in history
            if r.get("word")
        }


class SessionManager:
    """conversation_id -> GameSession 内存映射。"""

    def __init__(self) -> None:
        self._sessions: dict[str, GameSession] = {}

    def get(self, conversation_id: str) -> Optional[GameSession]:
        s = self._sessions.get(conversation_id)
        if s:
            s.touch()
        return s

    def set(self, session: GameSession) -> None:
        self._sessions[session.conversation_id] = session

    def drop(self, conversation_id: str) -> None:
        self._sessions.pop(conversation_id, None)

    def __len__(self) -> int:
        return len(self._sessions)
