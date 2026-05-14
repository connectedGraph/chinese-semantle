"""
猜词 API 服务入口。

启动方式
--------
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 8000

部署到 Vercel
-------------
    入口在 api/index.py，通过 Mangum 适配 ASGI。

环境变量
--------
- SEMANTLE_ENGINE：local | light | auto（默认 auto）
- PUZZLE_SECRET：HMAC 签名密钥（生产必填）
- DATABASE_URL：Neon Postgres 连接串（不填则用进程内 MemoryRepo）
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .engine_base import get_runtime_engine
from .game import GameStore, MAX_EXTRA_HINTS
from .leaderboard import (
    LeaderboardEntry,
    get_repo,
    issue_submit_token,
    verify_submit_token,
)
from .leaderboard.repo import normalize_nickname
from .leaderboard.tokens import make_payload
from .models import (
    CreateGameRequest,
    CreateGameResponse,
    GameSummary,
    GuessRequest,
    GuessResponse,
    LeaderboardEntryOut,
    LeaderboardOut,
    PuzzlePeekResponse,
    RequestHintResponse,
    SubmitScoreRequest,
    SubmitScoreResponse,
)
from .puzzle_codes import decode_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ------- 全局对象（lifespan 中初始化）-------

store: GameStore  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    logger.info("Booting word-vector engine…")
    engine = get_runtime_engine()
    store = GameStore(engine)
    logger.info("Engine ready. Vocab size = %d", engine.vocab_size)
    # 排行榜：异步初始化（Postgres 建表）；本地 Memory 是 no-op
    try:
        await get_repo().init()
    except Exception as e:  # noqa: BLE001
        logger.warning("Leaderboard init failed (will retry on use): %s", e)
    yield
    logger.info("Shutting down.")
    try:
        await get_repo().close()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(
    title="Chinese Semantle API",
    description=(
        "中文版 Semantle 猜词游戏后端服务。\n\n"
        "- 提供随机/指定谜底开局、猜词、提示、放弃、排行榜等接口\n"
        "- 同一个谜底词永远对应同一个 `puzzle_code`，可用 `?game=XXXXXX` 分享\n"
        "- 排行榜需在 `guess` 响应下发 `submit_token` 后才能提交，且只接受未使用提示的成绩"
    ),
    version="0.2.0",
    lifespan=lifespan,
    # 关闭默认 /docs，使用自定义版本（与首页同设计风格）
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------- 工具 -------

def _ensure_game(game_id: str):
    g = store.get(game_id)
    if not g:
        raise HTTPException(404, f"Game not found: {game_id}")
    return g


def _maybe_issue_submit_token(game) -> Optional[str]:
    """猜中谜底 + 本局从未用过提示，才下发 token。"""
    if not game.is_finished:
        return None
    if game.hint_ever_used:
        return None
    payload = make_payload(
        puzzle_code=game.puzzle_code,
        guess_count=game.guess_count,
        game_id=game.id,
    )
    return issue_submit_token(payload)


# ------- 路由：根 / 健康 -------

# 自定义 API 文档页：保留 Swagger UI 的功能，但用项目自有 CSS 重写视觉
# 设计语言与前端 /styles.css 完全对齐。CSS 直接读 frontend/docs.css 并内联，
# 避免本地 dev (8000) 与前端静态服务 (5173) 跨源的麻烦。
_DOCS_CSS_PATH = (
    Path(__file__).resolve().parent.parent.parent / "frontend" / "docs.css"
)


def _load_docs_css() -> str:
    try:
        return _DOCS_CSS_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("docs.css not found at %s; serving plain Swagger UI.", _DOCS_CSS_PATH)
        return ""


_DOCS_CSS_CACHE: Optional[str] = None


def _custom_docs_html() -> str:
    global _DOCS_CSS_CACHE
    if _DOCS_CSS_CACHE is None:
        _DOCS_CSS_CACHE = _load_docs_css()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>API 文档 · 猜词</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css" />
  <style>{_DOCS_CSS_CACHE}</style>
</head>
<body>
  <header class="cs-header">
    <div class="cs-brand">
      <span class="cs-brand-mark"></span>
      <h1>API 文档</h1>
      <span class="cs-sub">Chinese Semantle</span>
    </div>
    <a class="cs-back" href="/">← 返回游戏</a>
  </header>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      docExpansion: "list",
      defaultModelsExpandDepth: 0,
      tryItOutEnabled: true,
      syntaxHighlight: {{ theme: "agate" }},
    }});
  </script>
</body>
</html>
"""


@app.get("/docs", include_in_schema=False)
def custom_docs():
    """自定义 Swagger UI 页面（与首页同设计风格）。"""
    return HTMLResponse(_custom_docs_html())


@app.get("/", tags=["meta"])
def root():
    return {
        "name": "Chinese Semantle API",
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/api/health", tags=["meta"])
def health():
    return {"ok": True, "vocab_size": store.engine.vocab_size}


# ------- 路由：游戏 -------

@app.post("/api/games", response_model=CreateGameResponse, tags=["game"])
def create_game(req: CreateGameRequest = CreateGameRequest()):
    """
    创建一局游戏。
    - 不传 `puzzle_code`：随机开一局
    - 传 `puzzle_code`：以指定编号对应的谜底开一局（用于分享 URL 进入）
    """
    if req.min_word_len > req.max_word_len:
        raise HTTPException(400, "min_word_len 不能大于 max_word_len")

    target_word: Optional[str] = None
    if req.puzzle_code:
        code = req.puzzle_code.upper().strip()
        target_word = decode_code(code)
        if not target_word:
            raise HTTPException(404, f"未知的 puzzle_code: {code}")

    try:
        game = store.create_game(
            hint_count=req.hint_count,
            min_word_len=req.min_word_len,
            max_word_len=req.max_word_len,
            target_word=target_word,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    return CreateGameResponse(
        game_id=game.id,
        puzzle_code=game.puzzle_code,
        hints=game.hints,
        target_length=len(game.target),
        created_at=game.created_at,
    )


@app.get(
    "/api/games/by-code/{puzzle_code}",
    response_model=CreateGameResponse,
    tags=["game"],
)
def create_game_by_code(puzzle_code: str):
    """便捷接口：按 puzzle_code 直接开一局（等价于 POST /api/games {puzzle_code}）。"""
    return create_game(CreateGameRequest(puzzle_code=puzzle_code))


@app.get("/api/games/{game_id}", response_model=GameSummary, tags=["game"])
def get_game(game_id: str):
    game = _ensure_game(game_id)
    return GameSummary(
        game_id=game.id,
        puzzle_code=game.puzzle_code,
        created_at=game.created_at,
        is_finished=game.is_finished,
        guess_count=game.guess_count,
        hints=game.hints,
        history=game.history,
        target=game.target if game.is_finished else None,
    )


@app.post("/api/games/{game_id}/guess", response_model=GuessResponse, tags=["game"])
def guess(game_id: str, req: GuessRequest):
    """
    提交一次猜词。

    机器人 / 多人场景：
    - `player_name` 可选，会写入 history 中的对应记录
    - 一局游戏支持多个玩家协作猜词，最终成绩归属由调用方约定（一般是触发猜中的玩家）

    若猜中且本局从未使用提示，响应里会附带 `submit_token`，用于排行榜提交。
    """
    game = _ensure_game(game_id)
    if game.is_finished:
        raise HTTPException(400, "本局游戏已结束")

    try:
        record = store.guess(game, req.word, player_name=req.player_name)
    except ValueError as e:
        raise HTTPException(422, str(e))

    return GuessResponse(
        record=record,
        is_finished=game.is_finished,
        guess_count=game.guess_count,
        submit_token=_maybe_issue_submit_token(game),
    )


@app.post("/api/games/{game_id}/giveup", response_model=GameSummary, tags=["game"])
def give_up(game_id: str):
    game = _ensure_game(game_id)
    game.is_finished = True
    return GameSummary(
        game_id=game.id,
        puzzle_code=game.puzzle_code,
        created_at=game.created_at,
        is_finished=True,
        guess_count=game.guess_count,
        hints=game.hints,
        history=game.history,
        target=game.target,
    )


@app.post("/api/games/{game_id}/hint", response_model=RequestHintResponse, tags=["game"])
def request_hint(game_id: str):
    """请求一个新的提示词。该提示等价于一次猜词，会写入 history。每局上限 MAX_EXTRA_HINTS 次。

    **使用提示后本局将永久失去排行榜资格**（即使后续不再用提示）。
    """
    game = _ensure_game(game_id)
    if game.is_finished:
        raise HTTPException(400, "本局游戏已结束")

    try:
        record = store.request_hint(game)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return RequestHintResponse(
        record=record,
        is_finished=game.is_finished,
        guess_count=game.guess_count,
        extra_hint_count=game.extra_hint_count,
        extra_hint_limit=MAX_EXTRA_HINTS,
    )


# ------- 路由：puzzle 元信息（不暴露谜底） -------

@app.get(
    "/api/puzzles/{puzzle_code}/peek",
    response_model=PuzzlePeekResponse,
    tags=["puzzle"],
)
def peek_puzzle(puzzle_code: str):
    """
    在不开局的情况下查询某 puzzle_code 是否存在 + 谜底字数。
    用于分享落地页校验编号合法性。**不返回谜底**。
    """
    code = puzzle_code.upper().strip()
    target = decode_code(code)
    if not target:
        return PuzzlePeekResponse(puzzle_code=code, target_length=0, exists=False)
    return PuzzlePeekResponse(
        puzzle_code=code, target_length=len(target), exists=True
    )


# ------- 路由：排行榜 -------

def _to_out(entries) -> list[LeaderboardEntryOut]:
    return [
        LeaderboardEntryOut(
            nickname=e.nickname,
            guess_count=e.guess_count,
            created_at=e.created_at,
        )
        for e in entries
    ]


@app.get(
    "/api/leaderboard/{puzzle_code}",
    response_model=LeaderboardOut,
    tags=["leaderboard"],
)
async def get_leaderboard(puzzle_code: str, limit: int = 3):
    """获取指定 puzzle_code 的 Top-N（默认 3）。"""
    code = puzzle_code.upper().strip()
    if not decode_code(code):
        raise HTTPException(404, f"未知的 puzzle_code: {code}")
    limit = max(1, min(50, limit))
    repo = get_repo()
    entries = await repo.top(code, limit=limit)
    stats = await repo.stats(code)
    return LeaderboardOut(
        puzzle_code=code,
        entries=_to_out(entries),
        plays=stats.get("plays"),
        best_guess_count=stats.get("best_guess_count"),
    )


@app.post(
    "/api/leaderboard/{puzzle_code}",
    response_model=SubmitScoreResponse,
    tags=["leaderboard"],
)
async def submit_score(puzzle_code: str, req: SubmitScoreRequest):
    """
    向排行榜提交成绩。

    校验流程：
    1. `submit_token` 验签 + 未过期
    2. token 中的 puzzle_code 与路径参数一致
    3. token 是在「未使用提示」时下发的（服务端保证：用过提示不会下发 token）

    昵称规范化：去空白；最长 12 中文字符；为空使用「匿名玩家」。
    """
    code = puzzle_code.upper().strip()
    payload = verify_submit_token(req.submit_token)
    if not payload:
        raise HTTPException(400, "submit_token 无效或已过期")
    if payload.puzzle_code != code:
        raise HTTPException(400, "submit_token 与 puzzle_code 不匹配")

    nickname = normalize_nickname(req.nickname)
    repo = get_repo()
    entry = LeaderboardEntry(
        puzzle_code=code,
        nickname=nickname,
        guess_count=payload.guess_count,
        hint_used=False,
        created_at=None,  # type: ignore
    )
    saved = await repo.submit(entry)

    # 计算本次成绩在排行榜中的排名（拿 Top-50 推算即可，超出认为 50+）
    top50 = await repo.top(code, limit=50)
    rank = None
    for i, e in enumerate(top50, 1):
        if e.id == saved.id:
            rank = i
            break

    stats = await repo.stats(code)
    return SubmitScoreResponse(
        ok=True,
        rank=rank,
        leaderboard=LeaderboardOut(
            puzzle_code=code,
            entries=_to_out(top50[:3]),
            plays=stats.get("plays"),
            best_guess_count=stats.get("best_guess_count"),
        ),
    )
