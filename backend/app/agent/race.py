"""
对战（Race）会话：人类与 Agent 猜同一个隐藏答案，谁先猜中谁赢。

- 人类：走 race 的 guess 接口，记录到 human_game
- Agent：后台 asyncio 任务跑 AgentRunner，记录到 agent_game
- 双方事件都在同一个 asyncio.Queue 里，/events 用 SSE 推送
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..game import Game, GameStore
from .deepseek import DeepSeekClient
from .runner import AgentRunner

logger = logging.getLogger(__name__)


class Race:
    def __init__(self, store: GameStore, human_game: Game, agent_game: Game, max_steps: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.store = store
        self.human_game = human_game
        self.agent_game = agent_game
        self.target = human_game.target
        self.max_steps = max_steps
        self.created_at = datetime.now(timezone.utc)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.started = False
        self.finished = False
        self.winner: Optional[str] = None  # "human" | "agent" | None
        self._stop = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[DeepSeekClient] = None

    # -------- 事件 --------

    async def emit(self, event: Dict[str, Any]) -> None:
        event.setdefault("race_id", self.id)
        await self.queue.put(event)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "race_id": self.id,
            "target_length": len(self.target),
            "target": self.target if self.finished else None,
            "started": self.started,
            "finished": self.finished,
            "winner": self.winner,
            "human_guesses": self.human_game.guess_count,
            "agent_guesses": self.agent_game.guess_count,
            "human_solved": self.human_game.is_finished,
            "agent_solved": self.agent_game.is_finished,
            "human_history": [r.model_dump(mode="json") for r in self.human_game.history],
            "agent_history": [r.model_dump(mode="json") for r in self.agent_game.history],
        }

    # -------- 人类猜词 --------

    async def human_guess(self, word: str) -> Dict[str, Any]:
        if self.finished:
            raise RuntimeError("本局已结束")
        rec = self.store.guess(self.human_game, word)
        await self.emit({
            "type": "human_guess",
            "record": rec.model_dump(mode="json"),
            "finished": self.human_game.is_finished,
        })
        if self.human_game.is_finished and not self.finished:
            await self._end("human")
        return {"record": rec.model_dump(mode="json"), **self.snapshot()}

    # -------- 启动 Agent --------

    async def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._task = asyncio.create_task(self._run_agent())

    async def _run_agent(self) -> None:
        try:
            self._client = DeepSeekClient()
            runner = AgentRunner(
                store=self.store,
                client=self._client,
                game=self.agent_game,
                emit=self._agent_emit,
                max_steps=self.max_steps,
                should_stop=lambda: self._stop or self.finished,
            )
            await runner.run()
        except Exception as e:  # noqa: BLE001
            logger.exception("agent run failed")
            await self.emit({"type": "error", "message": f"Agent 异常：{e}"})
            await self._end(None)

    async def _agent_emit(self, event: Dict[str, Any]) -> None:
        await self.emit(event)
        if event.get("type") == "finish" and event.get("solved") and not self.finished:
            await self._end("agent")

    async def _end(self, winner: Optional[str]) -> None:
        if self.finished:
            return
        self.finished = True
        self.winner = winner
        self._stop = True
        await self.emit({
            "type": "race_end",
            "winner": winner,
            "target": self.target,
            "human_guesses": self.human_game.guess_count,
            "agent_guesses": self.agent_game.guess_count,
        })

    async def close(self) -> None:
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    # -------- SSE --------

    async def stream(self) -> AsyncGenerator[str, None]:
        # 先补发快照，方便晚连的客户端
        yield _sse({"type": "snapshot", "race_id": self.id, **self.snapshot()})
        while True:
            try:
                ev = await asyncio.wait_for(self.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse(ev)
            if ev.get("type") == "race_end":
                # 结束后再等一小会儿确保事件已消费，然后结束流
                await asyncio.sleep(0.05)
                return


def _sse(obj: Dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


class RaceManager:
    def __init__(self, store: GameStore) -> None:
        self.store = store
        self._races: Dict[str, Race] = {}

    def create(
        self,
        min_word_len: int = 2,
        max_word_len: int = 4,
        target_word: Optional[str] = None,
        max_steps: int = 12,
    ) -> Race:
        # 两边用同一个谜底：先开一局拿到 target，再为 Agent 开同 target 的一局
        base = self.store.create_game(
            min_word_len=min_word_len, max_word_len=max_word_len,
            target_word=target_word, source="shared",
        )
        agent_game = self.store.create_game(
            target_word=base.target, source="shared",
        )
        race = Race(self.store, base, agent_game, max_steps=max_steps)
        self._races[race.id] = race
        return race

    def get(self, race_id: str) -> Optional[Race]:
        return self._races.get(race_id)

    async def close_all(self) -> None:
        for r in list(self._races.values()):
            await r.close()
