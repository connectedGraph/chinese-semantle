"""
submit_token —— 提交排行榜的服务端签名凭证。

为什么需要？
-----------
排行榜规则：「只允许 hint_used=0 的玩家提交」。但前端可信度低——任何人都可以
绕过前端直接 POST 排行榜接口伪造 guess_count。

解决：玩家「猜中谜底」的瞬间，服务端在 guess 响应里下发一个 HMAC 签名 token，
打包：(puzzle_code, guess_count, game_id, exp)。提交排行榜时，客户端必须带上
这个 token；服务端验签 + 校验未过期 + 验证 guess_count 是否一致。

Token 只在「猜中且本局未使用过任何提示」的情况下下发。这样：
- 用过提示的玩家根本拿不到 token → 不能提交
- guess_count 由服务端签发，客户端篡改后验签失败
- exp 限定 1 小时，避免长期复用

注意：因为 LightEngine 是无状态的（每次请求都重建 Game），「未使用过提示」这个
判断在 game.py 完成猜中检查时记一个变量即可——服务端在这条响应的生命周期内可信。
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Optional

logger = logging.getLogger(__name__)

# 与 puzzle_codes 共用同一个密钥，避免再加一个环境变量
# 也可单独配置 LEADERBOARD_SECRET 覆盖
_TOKEN_TTL_SEC = 3600  # 1 小时


def _get_token_secret() -> bytes:
    return (
        os.environ.get("LEADERBOARD_SECRET")
        or os.environ.get("PUZZLE_SECRET")
        or "spacekid-semantle-dev-secret-do-not-use-in-prod"
    ).encode("utf-8")


@dataclass
class SubmitTokenPayload:
    puzzle_code: str
    guess_count: int
    game_id: str
    exp: int  # unix seconds


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_submit_token(payload: SubmitTokenPayload) -> str:
    """
    生成 token：base64url(json) + "." + base64url(HMAC-SHA256)
    （类似 JWT 但简化版，不带 header；只用于内部接口）
    """
    body = {
        "c": payload.puzzle_code,
        "g": payload.guess_count,
        "id": payload.game_id,
        "exp": payload.exp,
    }
    body_bytes = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_get_token_secret(), body_bytes, sha256).digest()
    return f"{_b64url(body_bytes)}.{_b64url(sig)}"


def verify_submit_token(token: str) -> Optional[SubmitTokenPayload]:
    """
    校验 token 合法性 + 未过期。返回 payload 或 None。
    """
    if not token or "." not in token:
        return None
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body_bytes = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return None

    expected = hmac.new(_get_token_secret(), body_bytes, sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        return None

    try:
        payload = SubmitTokenPayload(
            puzzle_code=str(body["c"]),
            guess_count=int(body["g"]),
            game_id=str(body["id"]),
            exp=int(body["exp"]),
        )
    except (KeyError, TypeError, ValueError):
        return None

    if payload.exp < int(time.time()):
        return None

    return payload


def make_payload(puzzle_code: str, guess_count: int, game_id: str) -> SubmitTokenPayload:
    return SubmitTokenPayload(
        puzzle_code=puzzle_code,
        guess_count=guess_count,
        game_id=game_id,
        exp=int(time.time()) + _TOKEN_TTL_SEC,
    )
