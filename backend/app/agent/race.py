"""
对战（Race）会话：人类与 Agent 猜同一个隐藏答案。

胜负判据：**猜中答案所用的猜测次数（guess_count）更少者胜**。
- LLM 回复快是速度优势，不参与比较；效率看"花了多少次猜测"。
- Agent 先猜中不会立即结束对局：它停下，人类继续，直到人类也猜中或放弃，再结算。
- 双方都猜中 → 次数少者胜；仅一方猜中 → 该方胜；都没猜中 → 平局。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

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
        self.winner: Optional[str] = None  # "human" | "agent" | "tie" | None
        # 双方各自的完成状态
        self.agent_done = False
        self.agent_solved = False
        self.agent_steps = 0
        self.human_done = False
        self.human_solved = False
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
            "human_done": self.human_done,
            "agent_done": self.agent_done,
            "agent_steps": self.agent_steps,
            "human_history": [r.model_dump(mode="json") for r in self.human_game.history],
            "agent_history": [r.model_dump(mode="json") for r in self.agent_game.history],
        }

    # -------- 人类猜词 --------

    async def human_guess(self, word: str) -> Dict[str, Any]:
        if self.human_done:
            raise RuntimeError("你这边已经结束")
        rec = self.store.guess(self.human_game, word)
        await self.emit({
            "type": "human_guess",
            "record": rec.model_dump(mode="json"),
            "finished": self.human_game.is_finished,
        })
        if self.human_game.is_finished:
            await self._human_finish(solved=True)
        return {"record": rec.model_dump(mode="json"), **self.snapshot()}

    async def human_giveup(self) -> Dict[str, Any]:
        if not self.human_done:
            self.human_game.is_finished = True
            self.human_game.give_up_ever = True
            await self._human_finish(solved=False)
        return self.snapshot()

    async def _human_finish(self, solved: bool) -> None:
        self.human_done = True
        self.human_solved = solved
        await self.emit({
            "type": "human_done",
            "solved": solved,
            "guesses": self.human_game.guess_count,
        })
        await self._maybe_settle()

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
            if not self.agent_done:
                self.agent_done = True
                await self.emit({"type": "agent_done", "solved": False,
                                 "guesses": self.agent_game.guess_count, "steps": self.agent_steps})
                await self._maybe_settle()

    async def _agent_emit(self, event: Dict[str, Any]) -> None:
        await self.emit(event)
        if event.get("type") == "finish" and not self.agent_done:
            self.agent_done = True
            self.agent_solved = bool(event.get("solved"))
            self.agent_steps = int(event.get("steps") or 0)
            await self.emit({
                "type": "agent_done",
                "solved": self.agent_solved,
                "guesses": self.agent_game.guess_count,
                "steps": self.agent_steps,
            })
            await self._maybe_settle()

    # -------- 结算 --------

    async def _maybe_settle(self) -> None:
        if self.finished:
            return
        if self.agent_done and self.human_done:
            await self._settle()

    async def _settle(self) -> None:
        hg, ag = self.human_game.guess_count, self.agent_game.guess_count
        hs, asolved = self.human_solved, self.agent_solved
        if hs and asolved:
            winner = "human" if hg < ag else "agent" if ag < hg else "tie"
        elif hs:
            winner = "human"
        elif asolved:
            winner = "agent"
        else:
            winner = "tie"
        await self._end(winner)

    async def _end(self, winner: Optional[str]) -> None:
        if self.finished:
            return
        self.finished = True
        self.winner = winner
        self._stop = True
        await self.emit({
            "type": "race_end",
            "winner": winner,
            "metric": "guesses",  # 按猜测次数比
            "target": self.target,
            "human_guesses": self.human_game.guess_count,
            "agent_guesses": self.agent_game.guess_count,
            "human_solved": self.human_solved,
            "agent_solved": self.agent_solved,
            "agent_steps": self.agent_steps,
        })

    async def close(self) -> None:
        self._stop = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except Exception:  # noqa: BLE001  (含 CancelledError / TimeoutError)
                pass
        if self._client:
            try:
                await asyncio.wait_for(self._client.aclose(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass

    # -------- SSE --------

    async def stream(self) -> AsyncGenerator[str, None]:
        yield _sse({"type": "snapshot", "race_id": self.id, **self.snapshot()})
        while True:
            try:
                ev = await asyncio.wait_for(self.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            yield _sse(ev)
            if ev.get("type") == "race_end":
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
        base = self.store.create_game(
            min_word_len=min_word_len, max_word_len=max_word_len,
            target_word=target_word, source="shared",
        )
        agent_game = self.store.create_game(target_word=base.target, source="shared")
        race = Race(self.store, base, agent_game, max_steps=max_steps)
        self._races[race.id] = race
        return race

    def get(self, race_id: str) -> Optional[Race]:
        return self._races.get(race_id)

    async def close_all(self) -> None:
        for r in list(self._races.values()):
            await r.close()
