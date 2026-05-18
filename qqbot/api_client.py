"""后端 HTTP 客户端：封装现有 FastAPI 接口。

不直接依赖 backend/* 任何模块，仅通过 HTTP 调用 ``api_base``。

关键特性：
- 自定义异常 :class:`GameNotFound` / :class:`PuzzleNotEncodable` / :class:`ApiError`
- :meth:`AsyncClient.guess_with_replay` 在 game_id 失效（404）时，
  自动用 puzzle_code 重建 game 并按缓存的 history 顺序重放，对调用方透明。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------- 异常 ----------

class ApiError(Exception):
    """通用后端错误。"""

    def __init__(self, status: int, detail: str, *, payload: Any = None):
        super().__init__(f"[{status}] {detail}")
        self.status = status
        self.detail = detail
        self.payload = payload


class GameNotFound(ApiError):
    """game_id 不存在（通常是后端进程冷启动后状态丢失）。"""


class PuzzleNotEncodable(ApiError):
    """自定义谜底创建失败（非白名单 / 不支持的词）。"""


class WordNotInVocab(ApiError):
    """猜词的词不在词向量词库中（HTTP 422 / detail 含'词向量中不存在该词'）。"""


# ---------- 客户端 ----------

class AsyncClient:
    def __init__(self, api_base: str, timeout: float = 15.0):
        self.api_base = api_base.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.api_base,
            timeout=timeout,
            headers={"User-Agent": "chinese-semantle-qqbot/0.1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # -------- 内部 --------

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ApiError(0, f"网络异常：{e}") from e

        if resp.status_code >= 400:
            try:
                payload = resp.json()
                detail = payload.get("detail") if isinstance(payload, dict) else None
                if isinstance(detail, list) and detail:
                    detail = detail[0].get("msg", str(detail))
                detail = detail or resp.text
            except Exception:
                payload = None
                detail = resp.text or f"HTTP {resp.status_code}"

            if resp.status_code == 404:
                raise GameNotFound(404, str(detail), payload=payload)
            if resp.status_code == 422 and "/puzzles/encode" in path:
                raise PuzzleNotEncodable(422, str(detail), payload=payload)
            if resp.status_code == 422 and "词向量中不存在该词" in str(detail):
                raise WordNotInVocab(422, str(detail), payload=payload)
            raise ApiError(resp.status_code, str(detail), payload=payload)

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # -------- 元信息 --------

    async def health(self) -> dict:
        return await self._request("GET", "/api/health")

    # -------- 游戏 --------

    async def create_random(self) -> dict:
        return await self._request(
            "POST", "/api/games", json={"mode": "random"}
        )

    async def create_daily(self, daily_date: str) -> dict:
        return await self._request(
            "POST",
            "/api/games",
            json={"mode": "daily", "daily_date": daily_date},
        )

    async def create_shared(self, puzzle_code: str) -> dict:
        """按 puzzle_code 开一局共享游戏（非计分）。"""
        return await self._request(
            "POST",
            "/api/games",
            json={"mode": "shared", "puzzle_code": puzzle_code},
        )

    async def get_game(self, game_id: str) -> dict:
        return await self._request("GET", f"/api/games/{game_id}")

    async def by_code(self, puzzle_code: str) -> dict:
        """按 puzzle_code 直接开局（等价于 create_shared 的便捷形式）。"""
        return await self._request(
            "GET", f"/api/games/by-code/{puzzle_code}"
        )

    async def guess(
        self, game_id: str, word: str, player_name: str | None
    ) -> dict:
        return await self._request(
            "POST",
            f"/api/games/{game_id}/guess",
            json={"word": word, "player_name": player_name},
        )

    async def hint(self, game_id: str) -> dict:
        return await self._request("POST", f"/api/games/{game_id}/hint")

    async def give_up(self, game_id: str) -> dict:
        return await self._request("POST", f"/api/games/{game_id}/giveup")

    # -------- 出题 / 每日 --------

    async def daily_today(self) -> dict:
        return await self._request("GET", "/api/daily/today")

    async def encode_puzzle(self, word: str) -> dict:
        return await self._request(
            "POST", "/api/puzzles/encode", json={"word": word}
        )

    async def peek_puzzle(self, puzzle_code: str) -> dict:
        return await self._request(
            "GET", f"/api/puzzles/{puzzle_code}/peek"
        )

    # -------- 高阶：404 自动重建 --------

    async def rebuild_and_replay(
        self,
        puzzle_code: str,
        history: list[dict],
    ) -> tuple[str, list[dict]]:
        """以 puzzle_code 重建一局 game，并按 history 顺序重放猜词。

        Returns:
            (new_game_id, replayed_history)
        """
        logger.warning(
            "[REBUILD] puzzle_code=%s, replay %d guesses (this means backend lost the game; "
            "session puzzle_code unchanged, target word unchanged)",
            puzzle_code,
            len(history),
        )
        created = await self.by_code(puzzle_code)
        new_gid = created["game_id"]
        logger.warning(
            "[REBUILD] new game_id=%s (puzzle_code=%s confirmed unchanged)",
            new_gid,
            created["puzzle_code"],
        )
        # 按 order 升序重放（防御性 sort）
        ordered = sorted(history, key=lambda r: r.get("order", 0))
        for rec in ordered:
            # 跳过 hint 类型的 record（提示由后端在 hint() 时写入；
            # 若我们在重建时把它当 guess 重放，会消耗一次正常猜词额度，
            # 但 hint 词通常不在猜词流里。这里保守处理：忽略 is_hint 记录）
            if rec.get("is_hint"):
                continue
            try:
                await self.guess(
                    new_gid,
                    rec["word"],
                    rec.get("player_name"),
                )
            except ApiError as e:
                logger.warning(
                    "[REBUILD] replay guess %r failed: %s; skip and continue",
                    rec.get("word"),
                    e,
                )
        # 拉一遍最新 summary（含权威 history）
        summary = await self.get_game(new_gid)
        return new_gid, summary.get("history", [])
