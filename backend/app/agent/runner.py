"""
Agent 猜词主循环。

负责：系统提示 → 调模型 → 执行（并行）工具 → 回填结果 → 直到猜中或步数用尽。
所有中间过程通过 `emit(event)` 异步回调推送给 SSE。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..game import Game, GameStore
from .deepseek import DeepSeekClient
from .tools import TOOL_SCHEMAS, ToolExecutor

logger = logging.getLogger(__name__)

EmitFn = Callable[[Dict[str, Any]], Awaitable[None]]

SYSTEM_PROMPT = """你是一名参加「中文语义猜词（Semantle）」比赛的 AI 选手。

规则：
- 有一个隐藏的中文目标词（字数已告知），你只能通过工具获取信息。
- 你只有两个工具：
  1) guess(words)：尝试猜词，一次可并行传入多个词。返回每个词与答案的语义相似度百分比和排名（rank 越小越接近答案，rank=1 代表这是最接近答案的词）。
  2) view_topk(k)：把你到目前为止已经猜过的词按接近度整理好返回（按相似度降序 / rank 升序，一行一词一相似度一rank）。k=-1 表示返回全部已猜词。它只是回顾整理，不产生新的猜测。
- 当某个词就是答案本身时，相似度为 100.00%。
- 目标是尽快猜中答案。请积极使用工具，一次多猜几个词以提高效率。

策略建议：
- 先并行猜一组语义上互不相同的常见词，快速判断方向；
- 根据相似度和 rank 收窄范围，对最接近的几个词继续发散联想；
- 猜了一批词后，可用 view_topk 回顾已猜结果，聚焦最接近的几个方向继续发散联想；
- 不要空谈，必须通过工具用数据决策。"""


def _system_message(target_length: int) -> Dict[str, Any]:
    return {
        "role": "system",
        "content": SYSTEM_PROMPT + f"\n\n本局目标词长度：{target_length} 个字。",
    }


class AgentRunner:
    def __init__(
        self,
        store: GameStore,
        client: DeepSeekClient,
        game: Game,
        emit: EmitFn,
        max_steps: int = 12,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.store = store
        self.client = client
        self.game = game
        self.emit = emit
        self.max_steps = max_steps
        self.should_stop = should_stop or (lambda: False)
        self.executor = ToolExecutor(store, game)

    async def _exec_one(self, tc: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个 tool_call，返回 {id,name,args,text,rows}。"""
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            text, rows = self.executor.execute(name, args)
            return {"id": tc.get("id"), "name": name, "args": args, "text": text, "rows": rows}
        except Exception as e:  # noqa: BLE001
            return {"id": tc.get("id"), "name": name, "args": args,
                    "text": f"工具执行失败：{e}", "rows": [], "error": str(e)}

    async def run(self) -> Dict[str, Any]:
        target_length = len(self.game.target)
        messages: List[Dict[str, Any]] = [
            _system_message(target_length),
            {"role": "user", "content": "比赛开始，请开始猜词。"},
        ]
        await self.emit({
            "type": "start",
            "model": self.client.model,
            "target_length": target_length,
            "max_steps": self.max_steps,
        })

        steps = 0
        for step in range(1, self.max_steps + 1):
            if self.should_stop() or self.game.is_finished:
                break
            steps = step
            try:
                res = await self.client.chat(messages, tools=TOOL_SCHEMAS)
            except Exception as e:  # noqa: BLE001
                await self.emit({"type": "error", "step": step, "message": str(e)})
                break

            await self.emit({
                "type": "assistant",
                "step": step,
                "content": res.content,
                "reasoning": res.reasoning,
                "finish_reason": res.finish_reason,
                "usage": res.usage,
            })

            if res.tool_calls:
                # 原样回填 assistant 的 tool_calls（OpenAI 协议）
                messages.append({
                    "role": "assistant",
                    "content": res.content or "",
                    "tool_calls": res.tool_calls,
                })
                # ★ 并行执行本回合的所有工具调用
                outs = await asyncio.gather(*[self._exec_one(tc) for tc in res.tool_calls])
                for out in outs:
                    await self.emit({
                        "type": "tool_result",
                        "step": step,
                        "id": out["id"],
                        "name": out["name"],
                        "args": out["args"],
                        "rows": out["rows"],
                        "error": out.get("error"),
                    })
                    if out["rows"]:
                        await self.emit({
                            "type": "guess_table",
                            "step": step,
                            "name": out["name"],
                            "rows": out["rows"],
                        })
                    # 回填给模型（截断极端长的内容，防御性）
                    messages.append({
                        "role": "tool",
                        "tool_call_id": out["id"],
                        "content": out["text"][:300_000],
                    })
            else:
                messages.append({"role": "assistant", "content": res.content or ""})
                if not self.game.is_finished:
                    messages.append({
                        "role": "user",
                        "content": "请继续使用工具猜词，直到猜中隐藏答案（相似度 100%）。",
                    })

            if self.game.is_finished:
                break
            if self.should_stop():
                break

        solved = self.game.is_finished
        result = {
            "type": "finish",
            "solved": solved,
            "steps": steps,
            "guesses": self.game.guess_count,
            "target": self.game.target if solved else None,
            "usage": self.client.total_usage,
        }
        await self.emit(result)
        return result
