"""Agent 对战相关路由。"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import get_manager

router = APIRouter(prefix="/api/agent", tags=["agent"])


class CreateRaceRequest(BaseModel):
    mode: Literal["versus", "challenge"] = Field(
        "versus", description="versus=人机对战；challenge=我出题给 Agent 猜（只有 Agent 猜）"
    )
    target_word: Optional[str] = Field(None, description="指定谜底（challenge 模式即用户出的题）；为空则随机")
    min_word_len: int = Field(2, ge=1, le=8)
    max_word_len: int = Field(2, ge=1, le=8, description="目标词最大长度，默认 2")
    max_steps: int = Field(12, ge=1, le=50, description="Agent 最多对话回合数")


class HumanGuessRequest(BaseModel):
    word: str = Field(..., min_length=1, max_length=16)


@router.post("/race")
async def create_race(req: CreateRaceRequest = CreateRaceRequest()):
    mgr = get_manager()
    if req.min_word_len > req.max_word_len:
        raise HTTPException(400, "min_word_len 不能大于 max_word_len")
    if req.target_word is not None:
        w = req.target_word.strip()
        if not w or len(w) > 8 or any(not ("\u4e00" <= ch <= "\u9fff") for ch in w):
            raise HTTPException(422, "谜底词必须是 1~8 个汉字")
        req.target_word = w
    try:
        race = mgr.create(
            min_word_len=req.min_word_len,
            max_word_len=req.max_word_len,
            target_word=req.target_word,
            max_steps=req.max_steps,
            solo=(req.mode == "challenge"),
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    return race.snapshot()


@router.post("/race/{race_id}/start")
async def start_race(race_id: str):
    race = get_manager().get(race_id)
    if not race:
        raise HTTPException(404, "race not found")
    await race.start()
    return {"ok": True, **race.snapshot()}


@router.get("/race/{race_id}")
async def get_race(race_id: str):
    race = get_manager().get(race_id)
    if not race:
        raise HTTPException(404, "race not found")
    return race.snapshot()


@router.post("/race/{race_id}/guess")
async def human_guess(race_id: str, req: HumanGuessRequest):
    race = get_manager().get(race_id)
    if not race:
        raise HTTPException(404, "race not found")
    if race.solo:
        raise HTTPException(400, "出题模式下人类不参与猜词")
    if race.human_done:
        raise HTTPException(400, "你这边已经结束（猜中或已放弃）")
    try:
        return await race.human_guess(req.word)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.post("/race/{race_id}/giveup")
async def giveup(race_id: str):
    race = get_manager().get(race_id)
    if not race:
        raise HTTPException(404, "race not found")
    if race.solo:
        raise HTTPException(400, "出题模式下没有人类放弃")
    return await race.human_giveup()


@router.get("/race/{race_id}/events")
async def race_events(race_id: str):
    race = get_manager().get(race_id)
    if not race:
        raise HTTPException(404, "race not found")
    return StreamingResponse(
        race.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
