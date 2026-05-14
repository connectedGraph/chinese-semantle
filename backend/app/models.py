"""Pydantic 数据模型：API 输入/输出契约。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------- 接近度等级 ----------

ProximityLevel = Literal["hot", "warm", "cold"]
"""
- hot:  Top-300，非常接近
- warm: Top-301~1000，接近
- cold: 不在 Top-1000，远
"""


# ---------- 单次猜词记录 ----------

class GuessRecord(BaseModel):
    order: int = Field(..., description="第几次猜测（从 1 开始）")
    word: str
    similarity: float = Field(..., description="原始余弦相似度，[-1, 1]")
    similarity_pct: float = Field(..., description="相似度百分比，余弦*100，保留 2 位")
    proximity_rank: Optional[int] = Field(
        None, description="接近度排名（在 Top-1000 内才有，1 表示最接近）"
    )
    proximity_level: ProximityLevel
    is_target: bool = False
    is_hint: bool = Field(
        False, description="True 表示这条是由「提示」按钮自动产生的猜词记录"
    )
    player_name: Optional[str] = Field(
        None, description="猜词玩家昵称（QQ 群等多人场景使用）"
    )
    guessed_at: datetime


# ---------- 请求 ----------

GameMode = Literal["random", "daily", "shared"]
"""
开局来源：
- random : 系统随机抽签（唯一可计分通道）
- daily  : 每日挑战 / 回溯（必须提供 daily_date）
- shared : 通过 puzzle_code 分享链接进入（必须提供 puzzle_code）
"""


class CreateGameRequest(BaseModel):
    hint_count: int = Field(0, ge=0, le=10, description="初始提示词数量（默认不给）")
    min_word_len: int = Field(2, ge=1, le=8)
    max_word_len: int = Field(4, ge=1, le=8)
    puzzle_code: Optional[str] = Field(
        None,
        description="指定谜底编号开局；为空时按 mode 处理",
        max_length=16,
    )
    mode: GameMode = Field(
        "random",
        description=(
            "开局来源；决定排行榜计分资格。"
            "默认 random（随机抽签，可计分）。"
            "传 daily 时需配套 daily_date；传 puzzle_code 时自动视为 shared。"
        ),
    )
    daily_date: Optional[str] = Field(
        None,
        description="每日挑战日期 YYYY-MM-DD（CN 时区），mode=daily 时必填",
        max_length=10,
    )


class GuessRequest(BaseModel):
    word: str = Field(..., min_length=1, max_length=16)
    player_name: Optional[str] = Field(None, max_length=64)


class SubmitScoreRequest(BaseModel):
    """向排行榜提交一次成绩。"""
    nickname: Optional[str] = Field(None, max_length=48, description="昵称，最多 12 中文字符；为空则使用「匿名玩家」")
    submit_token: str = Field(..., description="由 guess 成功响应下发的签名 token")

    @field_validator("nickname")
    @classmethod
    def _strip_nickname(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None


# ---------- 响应 ----------

class HintWord(BaseModel):
    """初始提示词：仅给出一个相似度档位的样例（不给坐标，避免泄露）。"""
    word: str
    similarity_pct: float
    proximity_level: ProximityLevel


class GameSummary(BaseModel):
    game_id: str
    puzzle_code: Optional[str] = Field(None, description="可分享的谜底编号")
    created_at: datetime
    is_finished: bool
    guess_count: int
    hints: List[HintWord]
    history: List[GuessRecord]
    target: Optional[str] = Field(None, description="仅在游戏结束/放弃后返回")
    source: GameMode = Field("random", description="本局来源")
    daily_date: Optional[str] = Field(None, description="每日挑战日期，仅 source=daily 时返回")
    is_scoring: bool = Field(True, description="本局当前是否计分")
    scoring_reason: str = Field("", description="计分/非计分原因")
    hint_ever_used: bool = False
    give_up_ever: bool = False


class CreateGameResponse(BaseModel):
    game_id: str
    puzzle_code: str = Field(..., description="可分享的谜底编号；URL 中 ?game=XXXXXX 使用")
    hints: List[HintWord]
    target_length: int = Field(..., description="答案的字数（用于在 UI 展示，避免猜超长词）")
    created_at: datetime
    source: GameMode = Field("random", description="本局来源（决定计分资格）")
    daily_date: Optional[str] = Field(None, description="每日挑战日期，仅 source=daily 时返回")
    is_scoring: bool = Field(True, description="本局当前是否计分（与 source / 提示 / 放弃综合判定）")
    scoring_reason: str = Field("", description="计分/非计分的具体原因（前端 hover tooltip 第一段）")


class GuessResponse(BaseModel):
    record: GuessRecord
    is_finished: bool
    guess_count: int
    submit_token: Optional[str] = Field(
        None,
        description="仅当玩家猜中谜底且本局从未使用提示时下发；提交排行榜时携带",
    )


class RequestHintResponse(BaseModel):
    """提示等价于一次猜词，因此响应结构与 GuessResponse 一致，并附带配额。"""
    record: GuessRecord
    is_finished: bool
    guess_count: int
    extra_hint_count: int = Field(..., description="本局已使用的手动提示次数")
    extra_hint_limit: int = Field(..., description="本局最多支持的手动提示次数")


# ---------- 排行榜 ----------

class LeaderboardEntryOut(BaseModel):
    nickname: str
    guess_count: int
    created_at: datetime


class LeaderboardOut(BaseModel):
    puzzle_code: str
    entries: List[LeaderboardEntryOut]
    plays: Optional[int] = Field(None, description="该谜底的总提交次数（null 表示未统计）")
    best_guess_count: Optional[int] = None


class SubmitScoreResponse(BaseModel):
    ok: bool
    rank: Optional[int] = Field(None, description="本次成绩在该谜底的排名（1-based）")
    leaderboard: LeaderboardOut


# ---------- Puzzle 元信息 ----------

class PuzzlePeekResponse(BaseModel):
    puzzle_code: str
    target_length: int
    exists: bool


# ---------- 每日挑战 ----------

class DailyTodayResponse(BaseModel):
    date: str = Field(..., description="今日日期，YYYY-MM-DD（Asia/Shanghai）")
    puzzle_code: str = Field(..., description="今日谜底对应的 puzzle_code")
    target_length: int


class DailyCalendarItem(BaseModel):
    date: str = Field(..., description="YYYY-MM-DD")
    is_published: bool = Field(..., description="是否已发布（在 [DAILY_LAUNCH_DATE, today] 内）")
    is_today: bool = Field(False, description="是否就是今日")
    puzzle_code: Optional[str] = Field(
        None,
        description="已发布日期的谜底编号（用于前端跳转 ?daily= 或 ?game=）；未发布为 null",
    )


class DailyCalendarResponse(BaseModel):
    today: str
    launch_date: str
    items: List[DailyCalendarItem]
