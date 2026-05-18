"""QQ 群机器人入口。

启动方式：
    cd /path/to/repo
    python -m qqbot.bot

依赖 ``qqbot/config.yaml`` 中的 appid/secret/api_base 配置（可被环境变量覆盖）。
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import botpy
from botpy.message import C2CMessage, GroupMessage

from .api_client import AsyncClient
from .config import Config, load_config
from .handlers import Handler, Reply
from .nicknames import NicknameStore
from .session import SessionManager
from .single_instance import AlreadyRunning, single_instance_guard

logger = logging.getLogger("qqbot")


class SemantleBotClient(botpy.Client):
    """中文猜词 QQ 机器人客户端。"""

    def __init__(self, *, config: Config, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._api_client: Optional[AsyncClient] = None
        self._handler: Optional[Handler] = None

    # ---- 生命周期 ----

    async def on_ready(self) -> None:
        # 初始化（on_ready 时已有 event loop）
        if self._api_client is None:
            self._api_client = AsyncClient(
                self._config.api_base, timeout=self._config.api_timeout
            )
            self._handler = Handler(
                config=self._config,
                api=self._api_client,
                sessions=SessionManager(),
                nicknames=NicknameStore(),
            )
            # 自检：拉一下后端 health
            try:
                health = await self._api_client.health()
                logger.info("后端连通性 OK：%s", health)
            except Exception as e:
                logger.warning("后端 health 自检失败：%s（仍继续运行）", e)

        bot_name = getattr(self.robot, "name", "Semantle-Bot")
        logger.info("Bot 「%s」 ready，API base=%s", bot_name, self._config.api_base)

    # ---- 群 @ 消息 ----

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        if self._handler is None:
            return
        user_openid = _get_group_user_openid(message)
        try:
            reply = await self._handler.handle_group_message(
                conversation_id=message.group_openid,
                text=message.content or "",
                user_openid=user_openid,
            )
        except Exception:
            logger.exception("group handler crashed")
            return
        if reply is None:
            return
        await self._send_group(message, reply)

    # ---- 私聊（C2C）消息 ----

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        if self._handler is None:
            return
        user_openid = message.author.user_openid
        try:
            reply = await self._handler.handle_c2c_message(
                conversation_id=user_openid,
                text=message.content or "",
                user_openid=user_openid,
            )
        except Exception:
            logger.exception("c2c handler crashed")
            return
        if reply is None:
            return
        await self._send_c2c(message, reply)

    # ---- 实际发送 ----

    async def _send_group(self, message: GroupMessage, reply: Reply) -> None:
        try:
            kwargs = _build_send_kwargs(reply, msg_id=message.id, msg_seq=1)
            await message._api.post_group_message(
                group_openid=message.group_openid, **kwargs
            )
        except Exception as e:
            # 40054005（消息被去重）= 同 msg_id+msg_seq 已发过；通常说明有多实例在跑。
            # fallback 纯文本时必须用 msg_seq=2 否则又会被去重。
            err = str(e)
            if "40054005" in err:
                logger.error(
                    "[DEDUP] group reply msg_id=%s 被去重，可能有多个 bot 实例在跑！"
                    " 请检查 `pgrep -f qqbot.bot`",
                    message.id,
                )
                return
            logger.warning("group send markdown failed: %s; fallback to text", e)
            try:
                await message._api.post_group_message(
                    group_openid=message.group_openid,
                    msg_type=0,
                    msg_id=message.id,
                    msg_seq=2,
                    content=_strip_md(reply.markdown),
                )
            except Exception:
                logger.exception("group fallback also failed")

    async def _send_c2c(self, message: C2CMessage, reply: Reply) -> None:
        try:
            kwargs = _build_send_kwargs(reply, msg_id=message.id, msg_seq=1)
            await message._api.post_c2c_message(
                openid=message.author.user_openid, **kwargs
            )
        except Exception as e:
            err = str(e)
            if "40054005" in err:
                logger.error(
                    "[DEDUP] c2c reply msg_id=%s 被去重，可能有多个 bot 实例在跑！"
                    " 请检查 `pgrep -f qqbot.bot`",
                    message.id,
                )
                return
            logger.warning("c2c send markdown failed: %s; fallback to text", e)
            try:
                await message._api.post_c2c_message(
                    openid=message.author.user_openid,
                    msg_type=0,
                    msg_id=message.id,
                    msg_seq=2,
                    content=_strip_md(reply.markdown),
                )
            except Exception:
                logger.exception("c2c fallback also failed")

    async def close(self) -> None:  # type: ignore[override]
        if self._api_client is not None:
            await self._api_client.aclose()
        await super().close()


# ---------- 辅助 ----------

def _build_send_kwargs(reply: Reply, *, msg_id: str, msg_seq: int = 1) -> dict:
    if reply.is_markdown:
        kwargs: dict = {
            "msg_type": 2,
            "msg_id": msg_id,
            "msg_seq": msg_seq,
            "markdown": {"content": reply.markdown},
            "content": " ",  # 占位，避免某些版本要求 content 非空
        }
        if reply.keyboard is not None:
            kwargs["keyboard"] = reply.keyboard
        return kwargs
    return {
        "msg_type": 0,
        "msg_id": msg_id,
        "msg_seq": msg_seq,
        "content": reply.markdown,
    }


def _strip_md(text: str) -> str:
    """markdown 失败时的纯文本兜底（粗暴去除常见 markdown 语法）。"""
    import re

    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    return text.strip()


def _get_group_user_openid(message: GroupMessage) -> str:
    """从群 @ 消息中提取 member_openid（群+用户维度的稳定标识）。"""
    author = getattr(message, "author", None)
    if author is not None:
        for field in ("member_openid", "user_openid"):
            v = getattr(author, field, None)
            if v:
                return str(v)
    return ""


# ---------- main ----------

def main() -> None:
    config = load_config()
    # botpy 自带 logging：禁用 botpy 默认的 TimedRotatingFileHandler，
    # 否则会在 cwd 生成 botpy.log（污染仓库根目录 / 部署目录）；
    # 控制台日志已足够，需要持久化时由 systemd/Docker 收集 stdout 即可。
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.getLogger("qqbot").setLevel(level)

    # 单实例守护：防止多个 bot 进程同时连 QQ 网关导致消息去重错乱
    try:
        with single_instance_guard():
            intents = botpy.Intents(public_messages=True)
            client = SemantleBotClient(
                config=config,
                intents=intents,
                is_sandbox=config.sandbox,
                log_level=level,
                ext_handlers=False,  # 禁用文件日志（避免 botpy.log 落盘到 cwd）
            )
            logger.info(
                "启动机器人，appid=%s, sandbox=%s, api_base=%s, pid=%d",
                config.appid,
                config.sandbox,
                config.api_base,
                os.getpid(),
            )
            try:
                client.run(
                    appid=config.appid,
                    secret=config.secret,
                )
            except KeyboardInterrupt:
                logger.info("收到 Ctrl+C，退出。")
            except Exception:
                logger.exception("Bot 异常退出")
                sys.exit(1)
    except AlreadyRunning as e:
        logger.error("%s", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
