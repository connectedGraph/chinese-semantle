"""
排行榜 Repo —— 抽象接口 + Memory（本地开发）+ Postgres（Neon 生产）。

环境变量
--------
- DATABASE_URL：Postgres 连接串（Neon 提供）。形如：
    postgres://user:pass@ep-xxxx.us-east-2.aws.neon.tech/dbname?sslmode=require
  设置则使用 PostgresRepo，否则使用 MemoryRepo（进程内字典，重启即失）。

表结构（PostgresRepo 启动时会自动 CREATE IF NOT EXISTS）
--------------------------------------------------------
    CREATE TABLE leaderboard (
        id           SERIAL PRIMARY KEY,
        puzzle_code  VARCHAR(16) NOT NULL,
        nickname     VARCHAR(48) NOT NULL,
        guess_count  INTEGER NOT NULL,
        hint_used    BOOLEAN NOT NULL DEFAULT FALSE,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX idx_leaderboard_rank
        ON leaderboard(puzzle_code, guess_count ASC, created_at ASC);

规则约束
--------
- 只接受 hint_used = FALSE 的记录（在 API 层用 submit_token 验签强制）
- 昵称：默认 "匿名玩家"；最长 48 字节（约 12 中文字符的 UTF-8 上限 + 余量）
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_NICKNAME = "匿名玩家"
NICKNAME_MAX_CHARS = 12  # 中文字符长度上限
NICKNAME_MAX_BYTES = 48  # DB 字段宽度（中文 UTF-8 3 字节，加余量）


@dataclass
class LeaderboardEntry:
    puzzle_code: str
    nickname: str
    guess_count: int
    created_at: datetime
    id: Optional[int] = None
    hint_used: bool = False


# ---------------- 抽象接口 ----------------

class LeaderboardRepo:
    """排行榜仓库的抽象基类。所有方法皆为协程，方便从 FastAPI 异步路由调用。"""

    async def init(self) -> None:
        """初始化（建表 / 加载连接池等）。重复调用应当幂等。"""
        return None

    async def close(self) -> None:
        return None

    async def submit(self, entry: LeaderboardEntry) -> LeaderboardEntry:  # pragma: no cover
        raise NotImplementedError

    async def top(self, puzzle_code: str, limit: int = 3) -> List[LeaderboardEntry]:  # pragma: no cover
        raise NotImplementedError

    async def stats(self, puzzle_code: str) -> dict:
        """返回 {plays, best_guess_count}，未提交过时 plays=0。"""
        top = await self.top(puzzle_code, limit=1)
        # 默认实现：基于 top 推断，子类可优化为单 SQL
        if not top:
            return {"plays": 0, "best_guess_count": None}
        return {"plays": None, "best_guess_count": top[0].guess_count}


# ---------------- 内存实现 ----------------

class MemoryLeaderboardRepo(LeaderboardRepo):
    """进程内字典存储；适合本地开发和 unit test。重启即丢。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 1
        self._entries: List[LeaderboardEntry] = []

    async def submit(self, entry: LeaderboardEntry) -> LeaderboardEntry:
        with self._lock:
            entry.id = self._next_id
            self._next_id += 1
            entry.created_at = entry.created_at or datetime.now(timezone.utc)
            self._entries.append(entry)
            return entry

    async def top(self, puzzle_code: str, limit: int = 3) -> List[LeaderboardEntry]:
        with self._lock:
            rows = [e for e in self._entries if e.puzzle_code == puzzle_code]
        rows.sort(key=lambda e: (e.guess_count, e.created_at))
        return rows[:limit]

    async def stats(self, puzzle_code: str) -> dict:
        with self._lock:
            rows = [e for e in self._entries if e.puzzle_code == puzzle_code]
        if not rows:
            return {"plays": 0, "best_guess_count": None}
        return {
            "plays": len(rows),
            "best_guess_count": min(e.guess_count for e in rows),
        }


# ---------------- Postgres 实现（懒加载） ----------------

class PostgresLeaderboardRepo(LeaderboardRepo):
    """
    基于 asyncpg 的 Postgres 实现。

    懒加载：import asyncpg 推迟到 init() 调用时，避免本地 dev 环境强制依赖。
    连接池：使用 asyncpg.Pool；并发查询安全。
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS leaderboard (
            id           SERIAL PRIMARY KEY,
            puzzle_code  VARCHAR(16) NOT NULL,
            nickname     VARCHAR(48) NOT NULL,
            guess_count  INTEGER NOT NULL,
            hint_used    BOOLEAN NOT NULL DEFAULT FALSE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_leaderboard_rank
            ON leaderboard(puzzle_code, guess_count ASC, created_at ASC);
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None  # type: ignore
        self._init_lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._init_lock:
            if self._pool is not None:
                return
            try:
                import asyncpg  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "DATABASE_URL is set but `asyncpg` is not installed. "
                    "Add `asyncpg` to requirements.txt."
                ) from e
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn, min_size=1, max_size=4, command_timeout=10
            )
            async with self._pool.acquire() as conn:
                await conn.execute(self._SCHEMA)
            logger.info("PostgresLeaderboardRepo initialized.")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def submit(self, entry: LeaderboardEntry) -> LeaderboardEntry:
        await self.init()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO leaderboard (puzzle_code, nickname, guess_count, hint_used)
                VALUES ($1, $2, $3, $4)
                RETURNING id, created_at
                """,
                entry.puzzle_code,
                entry.nickname,
                entry.guess_count,
                entry.hint_used,
            )
            entry.id = row["id"]
            entry.created_at = row["created_at"]
            return entry

    async def top(self, puzzle_code: str, limit: int = 3) -> List[LeaderboardEntry]:
        await self.init()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, puzzle_code, nickname, guess_count, hint_used, created_at
                FROM leaderboard
                WHERE puzzle_code = $1
                ORDER BY guess_count ASC, created_at ASC
                LIMIT $2
                """,
                puzzle_code, limit,
            )
        return [
            LeaderboardEntry(
                id=r["id"],
                puzzle_code=r["puzzle_code"],
                nickname=r["nickname"],
                guess_count=r["guess_count"],
                hint_used=r["hint_used"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    async def stats(self, puzzle_code: str) -> dict:
        await self.init()
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS plays, MIN(guess_count) AS best
                FROM leaderboard
                WHERE puzzle_code = $1
                """,
                puzzle_code,
            )
        return {
            "plays": int(row["plays"] or 0),
            "best_guess_count": int(row["best"]) if row["best"] is not None else None,
        }


# ---------------- 工厂 ----------------

_repo_singleton: Optional[LeaderboardRepo] = None


def get_repo() -> LeaderboardRepo:
    """
    根据 DATABASE_URL 选择实现。单例。
    - 未设置 DATABASE_URL → MemoryRepo
    - 设置 → PostgresRepo（首次 init 在第一次调用时完成）
    """
    global _repo_singleton
    if _repo_singleton is not None:
        return _repo_singleton

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn:
        logger.info("Using PostgresLeaderboardRepo (Neon / Postgres).")
        # Neon 默认 DSN 一般已含 sslmode=require；不再二次拼装
        # Vercel 上有些场景 DATABASE_URL 是 postgresql://，asyncpg 同样兼容
        _repo_singleton = PostgresLeaderboardRepo(dsn)
    else:
        logger.warning(
            "DATABASE_URL not set. Using MemoryLeaderboardRepo "
            "(scores will be lost on restart)."
        )
        _repo_singleton = MemoryLeaderboardRepo()
    return _repo_singleton


def normalize_nickname(raw: Optional[str]) -> str:
    """
    规范化昵称：
    - None / 空字符串 / 全空白 → DEFAULT_NICKNAME
    - 长度超过 12 中文字符 → 截断
    - 去除前后空白
    """
    if not raw:
        return DEFAULT_NICKNAME
    name = raw.strip()
    if not name:
        return DEFAULT_NICKNAME
    if len(name) > NICKNAME_MAX_CHARS:
        name = name[:NICKNAME_MAX_CHARS]
    # 防御性：避免超出 DB VARCHAR 字节宽度
    encoded = name.encode("utf-8")
    if len(encoded) > NICKNAME_MAX_BYTES:
        # 逐字符切到字节安全长度
        while len(name.encode("utf-8")) > NICKNAME_MAX_BYTES:
            name = name[:-1]
    return name
