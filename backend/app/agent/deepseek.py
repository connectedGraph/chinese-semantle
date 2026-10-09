"""
极简 DeepSeek Chat Completions 客户端（OpenAI 兼容）。

只做这一件事：发 messages + tools，拿回 message（可能带 tool_calls）。
不引入 openai SDK，方便放进现有精简依赖。

环境变量：
- DEEPSEEK_API_KEY   （必填）
- DEEPSEEK_BASE_URL  默认 https://api.deepseek.com
- DEEPSEEK_MODEL     默认 deepseek-flash
- DEEPSEEK_PROXY     可选，http(s)://... ；不设则遵循 HTTP(S)_PROXY，再不行直连
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


@dataclass
class ChatResult:
    content: str
    reasoning: str
    tool_calls: List[Dict[str, Any]]
    finish_reason: str
    usage: Dict[str, Any]


class DeepSeekClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout: float = 300.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY 环境变量")
        self.base_url = (base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
        proxy = proxy if proxy is not None else os.environ.get("DEEPSEEK_PROXY")
        # DEEPSEEK_PROXY="" 显式禁用代理
        if proxy == "":
            self._client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        elif proxy:
            self._client = httpx.AsyncClient(timeout=timeout, proxy=proxy)
        else:
            self._client = httpx.AsyncClient(timeout=timeout)
        self._total_usage: Dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }

    @property
    def total_usage(self) -> Dict[str, int]:
        return dict(self._total_usage)

    def _accumulate(self, usage: Dict[str, Any]) -> None:
        for k in self._total_usage:
            self._total_usage[k] += int(usage.get(k) or 0)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.6,
        max_tokens: int = 4096,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        url = f"{self.base_url}/chat/completions"
        resp = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"DeepSeek API {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        self._accumulate(data.get("usage") or {})

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        return ChatResult(
            content=msg.get("content") or "",
            reasoning=msg.get("reasoning_content") or "",
            tool_calls=msg.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason") or "",
            usage=data.get("usage") or {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
