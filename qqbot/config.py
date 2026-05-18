"""配置加载：YAML 文件 + 环境变量覆盖。

环境变量（用于服务器部署 / 不落盘场景）：
  - QQBOT_APPID
  - QQBOT_APPSECRET
  - QQBOT_API_BASE
  - QQBOT_SANDBOX        ("true"/"false")
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass
class Config:
    appid: str
    secret: str
    sandbox: bool
    api_base: str
    share_url_prefix: str
    history_top_n: int
    session_history_cap: int
    api_timeout: int
    log_level: str

    def share_url(self, puzzle_code: str) -> str:
        return f"{self.share_url_prefix}{puzzle_code}"


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return default


def load_config(path: Path | str | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    # 环境变量覆盖（部署时优先）
    appid = os.environ.get("QQBOT_APPID") or str(raw.get("appid") or "")
    secret = os.environ.get("QQBOT_APPSECRET") or str(raw.get("secret") or "")
    api_base = os.environ.get("QQBOT_API_BASE") or str(
        raw.get("api_base") or "https://semantle.spacekid.me"
    )
    sandbox_env = os.environ.get("QQBOT_SANDBOX")
    sandbox = _as_bool(sandbox_env, _as_bool(raw.get("sandbox"), False))

    share_url_prefix = str(
        raw.get("share_url_prefix") or "https://semantle.spacekid.me/?game="
    )
    history_top_n = int(raw.get("history_top_n") or 10)
    session_history_cap = int(raw.get("session_history_cap") or 500)
    api_timeout = int(raw.get("api_timeout") or 15)
    log_level = str(raw.get("log_level") or "INFO").upper()

    if not appid or not secret:
        raise RuntimeError(
            "缺少 appid / secret。请在 qqbot/config.yaml 中配置，"
            "或通过 QQBOT_APPID / QQBOT_APPSECRET 环境变量注入。"
        )

    # api_base 末尾去掉斜杠，统一拼接
    api_base = api_base.rstrip("/")

    return Config(
        appid=appid,
        secret=secret,
        sandbox=sandbox,
        api_base=api_base,
        share_url_prefix=share_url_prefix,
        history_top_n=history_top_n,
        session_history_cap=session_history_cap,
        api_timeout=api_timeout,
        log_level=log_level,
    )
