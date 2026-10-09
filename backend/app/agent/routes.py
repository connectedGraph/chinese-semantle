"""Agent 对战相关路由。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import get_manager

router = APIRouter(prefix="/api/agent", tags=["agent"])


class CreateRaceRequest(BaseModel):
    target_word: Optional[str] = Field(None, description="指定谜底（调试用）；为空则随机")
    min_word_len: int = Field(2, ge=1, le=8)
    max_word_len: int = Field(4, ge=1, le=8)
    max_steps: int = Field(12, ge=1, le=50, description="Agent 最多对话回合数")


class HumanGuessRequest(BaseModel):
    word: str = Field(..., min_length=1, max_length=16)


@router.post("/race")
async def create_race(req: CreateRaceRequest = CreateRaceRequest()):
    mgr = get_manager()
    if req.min_word_len > req.max_word_len:
        raise HTTPException(400, "min_word_len 不能大于 max_word_len")
    try:
        race = mgr.create(
            min_word_len=req.min_word_len,
            max_word_len=req.max_word_len,
            target_word=req.target_word,
            max_steps=req.max_steps,
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
