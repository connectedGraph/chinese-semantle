"""命令解析 + 业务分发。

输入：清洗过的纯文本（已去除 @bot mention 与首尾空白）+ 会话上下文。
输出：通过 Reply 协议把 markdown / keyboard 交给 bot.py 实际发送。

为让 bot.py 与 botpy SDK 解耦，handlers 不直接调 message.reply，
而是返回 :class:`Reply` 描述符，由 bot.py 翻译成 ``message.reply(...)``。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, parse_qs

from . import keyboard as kb
from . import renderer as r
from .api_client import (
    ApiError,
    AsyncClient,
    GameNotFound,
    PuzzleNotEncodable,
    WordNotInVocab,
)
from .config import Config
from .nicknames import NicknameStore
from .session import GameSession, SessionManager

logger = logging.getLogger(__name__)


# ---------- Reply 描述符 ----------

@dataclass
class Reply:
    """bot.py 会把它翻译成 message.reply(msg_type=2, markdown=..., keyboard=...)。"""

    markdown: str
    keyboard: Optional[dict] = None
    # 是否使用 markdown 消息（False = 纯文本兜底）
    is_markdown: bool = True


# ---------- 输入预处理 ----------

# 去除 botpy 不会自动剥离的可能残留 mention（保险起见）
_MENTION_RE = re.compile(r"<@!?\d+>")
# 命令前缀
_CMDS = {
    "/新游戏": "new",
    "/随机计分游戏": "new",
    "/每日挑战": "daily",
    "/指定游戏": "shared",  # 后跟 url 或 puzzle_code
    "/提示": "hint",
    "/放弃": "giveup",
    "/出题": "encode",  # 仅私聊
    "/改名": "rename",
    "/改昵称": "rename",
    "/帮助": "help",
    "/help": "help",
}

_CN_RE = re.compile(r"^[\u4e00-\u9fff]{1,8}$")
_CODE_RE = re.compile(r"^[A-Z2-7]{6,8}$")


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = _MENTION_RE.sub("", raw)
    return text.strip()


def parse_command(text: str) -> tuple[str, str]:
    """返回 (command, rest)。command 为空字符串表示非命令（猜词或无效输入）。"""
    if not text.startswith("/"):
        return "", text
    # 分离命令头与参数
    parts = text.split(maxsplit=1)
    head = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""
    cmd = _CMDS.get(head, "")
    return cmd, rest


def extract_puzzle_code(arg: str) -> Optional[str]:
    """从 /指定游戏 后的参数中提取 puzzle_code。

    支持：
      - 纯编号 MBX07X
      - 完整 URL 含 ?game=MBX07X
      - 形如 #MBX07X / 带空白
    """
    if not arg:
        return None
    s = arg.strip().lstrip("#").strip()

    # 先尝试 URL 解析
    if "://" in s or s.startswith("//"):
        try:
            u = urlparse(s if "://" in s else "https:" + s)
            qs = parse_qs(u.query or "")
            for key in ("game", "code", "puzzle"):
                if key in qs and qs[key]:
                    candidate = qs[key][0].strip().upper()
                    if _CODE_RE.match(candidate):
                        return candidate
        except Exception:
            pass

    # 也兼容 ?game=XXX 直接出现在文本中（即使不是合法 URL）
    m = re.search(r"[?&](?:game|code|puzzle)=([A-Za-z0-9]+)", s)
    if m:
        candidate = m.group(1).upper()
        if _CODE_RE.match(candidate):
            return candidate

    # 纯编号
    candidate = s.upper()
    if _CODE_RE.match(candidate):
        return candidate
    return None


# ---------- Handler 实例 ----------

class Handler:
    def __init__(
        self,
        config: Config,
        api: AsyncClient,
        sessions: SessionManager,
        nicknames: NicknameStore,
    ) -> None:
        self.config = config
        self.api = api
        self.sessions = sessions
        self.nicknames = nicknames

    # ---- 公共调度入口 ----

    async def handle_group_message(
        self,
        conversation_id: str,
        text: str,
        user_openid: str,
        *,
        is_private: bool = False,
    ) -> Optional[Reply]:
        """处理群 @ 消息（也被私聊调度间接复用）。

        ``conversation_id`` = group_openid（一个群一局）
        ``user_openid`` = member_openid（用于昵称解析）
        ``is_private`` = 调用方是 c2c 时 True，影响"猜词非汉字"提示策略
        """
        text = clean_text(text)
        if not text:
            return None  # 空消息忽略
        cmd, rest = parse_command(text)
        scope = "user" if is_private else "group"
        player_name = self._resolve_name(user_openid, scope=scope)
        try:
            if cmd == "rename":
                return await self._cmd_rename(user_openid, rest)
            if cmd == "new":
                return await self._cmd_new(conversation_id, "random")
            if cmd == "daily":
                return await self._cmd_new(conversation_id, "daily")
            if cmd == "shared":
                code = extract_puzzle_code(rest)
                if not code:
                    return Reply(
                        r.render_error(
                            "未识别游戏编号或链接。用法：`/指定游戏 QT5PWS` 或 `/指定游戏 https://semantle.spacekid.me/?game=QT5PWS`"
                        )
                    )
                return await self._cmd_new(
                    conversation_id, "shared", puzzle_code=code
                )
            if cmd == "hint":
                return await self._cmd_hint(conversation_id, player_name)
            if cmd == "giveup":
                return await self._cmd_giveup(conversation_id)
            if cmd == "help":
                return Reply(r.render_help())
            if cmd == "encode":
                return Reply(
                    r.render_error(
                        "/出题 仅支持私聊使用。请私聊我并发送 `/出题 你的词`～"
                    )
                )
            if cmd:
                # 命令头无效但带 /
                return Reply(r.render_error(f"未识别的命令：`{text.split()[0]}`。发送 `/帮助` 查看用法。"))

            # 非命令 -> 当猜词处理
            return await self._cmd_guess(
                conversation_id, text, player_name, is_private=is_private
            )
        except ApiError as e:
            logger.exception("API error")
            return Reply(r.render_error(f"后端开小差：{e.detail}"))
        except Exception as e:  # pragma: no cover
            logger.exception("unexpected error")
            return Reply(r.render_error(f"出错了：{e}"))

    async def handle_c2c_message(
        self,
        conversation_id: str,
        text: str,
        user_openid: str,
    ) -> Optional[Reply]:
        """处理私聊消息。私聊允许所有群命令 + /出题。

        ``conversation_id`` = ``user_openid``（私聊一人一局）
        """
        text = clean_text(text)
        if not text:
            return None
        cmd, rest = parse_command(text)
        try:
            if cmd == "encode":
                return await self._cmd_encode(rest)
            if cmd == "help":
                return Reply(r.render_help())
            # 其余命令复用群处理（带 is_private=True）
            return await self.handle_group_message(
                conversation_id, text, user_openid, is_private=True
            )
        except ApiError as e:
            logger.exception("API error (c2c)")
            return Reply(r.render_error(f"后端开小差：{e.detail}"))
        except Exception as e:  # pragma: no cover
            logger.exception("unexpected error (c2c)")
            return Reply(r.render_error(f"出错了：{e}"))

    # ---- 各命令实现 ----

    def _resolve_name(self, openid: str, *, scope: str) -> str:
        nick = self.nicknames.get(openid)
        if nick:
            return nick
        return self.nicknames.fallback_name(openid, scope=scope)

    async def _cmd_rename(self, user_openid: str, arg: str) -> Reply:
        name = arg.strip()
        if not name:
            await self.nicknames.clear(user_openid)
            return Reply(r.render_nickname_cleared())
        # 限制：1-12 字符（NicknameStore 自动截断）
        saved = await self.nicknames.set(user_openid, name)
        return Reply(r.render_nickname_set(saved))

    async def _cmd_new(
        self,
        conversation_id: str,
        mode: str,
        *,
        puzzle_code: Optional[str] = None,
    ) -> Reply:
        if mode == "random":
            data = await self.api.create_random()
        elif mode == "daily":
            today = await self.api.daily_today()
            data = await self.api.create_daily(today["date"])
        elif mode == "shared":
            assert puzzle_code is not None
            try:
                data = await self.api.create_shared(puzzle_code)
            except ApiError as e:
                if e.status in (400, 404, 422):
                    return Reply(
                        r.render_error(f"无法用编号 `{puzzle_code}` 开局：{e.detail}")
                    )
                raise
        else:
            return Reply(r.render_error("未知 mode"))

        session = GameSession(
            conversation_id=conversation_id,
            game_id=data["game_id"],
            puzzle_code=data["puzzle_code"],
            target_length=data["target_length"],
            source=data.get("source", mode),
            is_scoring=False,  # bot 模式一律不计分
            daily_date=data.get("daily_date"),
        )
        self.sessions.set(session)
        logger.info(
            "[NEW_GAME] conv=%s mode=%s puzzle=%s game_id=%s target_length=%d",
            conversation_id,
            mode,
            session.puzzle_code,
            session.game_id,
            session.target_length,
        )
        md = r.render_game_created(
            puzzle_code=session.puzzle_code,
            target_length=session.target_length,
            source=session.source,
            daily_date=session.daily_date,
        )
        return Reply(md)

    async def _cmd_guess(
        self,
        conversation_id: str,
        text: str,
        player_name: str,
        *,
        is_private: bool = False,
    ) -> Optional[Reply]:
        session = self.sessions.get(conversation_id)
        if not session:
            return Reply(r.render_no_game_yet())
        if session.is_finished:
            return Reply(
                r.render_already_finished(
                    session.puzzle_code, session.target_word
                )
            )

        # 猜词只接受 1-8 个汉字
        # - 私聊：明确告知（私聊不存在刷屏问题，且用户更需要明确反馈）
        # - 群聊：静默忽略，避免群里刷屏
        if not _CN_RE.match(text):
            if is_private:
                return Reply(
                    r.render_error(
                        f"不支持「{text}」，猜词请输入 1-8 个汉字。"
                    )
                )
            return None

        logger.info(
            "[GUESS] conv=%s puzzle=%s game_id=%s word=%s player=%s",
            conversation_id,
            session.puzzle_code,
            session.game_id,
            text,
            player_name,
        )

        async with session.lock:
            # 同 puzzle 同词去重：直接复用历史结果，不消耗后端 guess_count
            cached = session.find_history(text)
            if cached:
                # 仅取一次 history 渲染当前列表，记录上一条 record 作为"最新一条"
                # 为了视觉提示"该词已猜过"，把 cached.record 标位 latest 返回
                # 但不写入新历史
                md = r.render_guess_response(
                    record=cached,
                    history=session.history,
                    puzzle_code=session.puzzle_code,
                    is_finished=False,
                    target_length=session.target_length,
                    top_n=self.config.history_top_n,
                )
                return Reply(
                    md
                    + f"\n\n_「{text}」之前已经猜过啦，直接复用上次结果～_"
                )

            # 调后端
            try:
                resp = await self.api.guess(session.game_id, text, player_name)
            except WordNotInVocab:
                # 词不在词向量词库 → 不消耗 guess_count，给友好提示
                return Reply(
                    r.render_error(
                        f"不支持「{text}」，请更换其他词汇尝试。"
                    )
                )
            except GameNotFound:
                # 后端 game 失效 -> 按 puzzle_code 重建 + 重放
                logger.info(
                    "game %s not found, rebuilding via puzzle_code %s",
                    session.game_id,
                    session.puzzle_code,
                )
                new_gid, replayed = await self.api.rebuild_and_replay(
                    session.puzzle_code, session.history
                )
                session.replace_game(game_id=new_gid, history=replayed)
                # 然后再发起本次猜词
                try:
                    resp = await self.api.guess(session.game_id, text, player_name)
                except WordNotInVocab:
                    return Reply(
                        r.render_error(
                            f"不支持「{text}」，请更换其他词汇尝试。"
                        )
                    )

            record = resp["record"]
            session.append_history(record)

            if resp.get("is_finished") and record.get("is_target"):
                session.is_finished = True
                session.target_word = record.get("word")
                md = r.render_guess_response(
                    record=record,
                    history=session.history,
                    puzzle_code=session.puzzle_code,
                    is_finished=True,
                    target_length=session.target_length,
                    top_n=self.config.history_top_n,
                )
                share_url = self.config.share_url(session.puzzle_code)
                return Reply(md, keyboard=kb.end_game_keyboard(share_url))

            md = r.render_guess_response(
                record=record,
                history=session.history,
                puzzle_code=session.puzzle_code,
                is_finished=False,
                target_length=session.target_length,
                top_n=self.config.history_top_n,
            )
            return Reply(md)

    async def _cmd_hint(
        self, conversation_id: str, player_name: str
    ) -> Reply:
        session = self.sessions.get(conversation_id)
        if not session:
            return Reply(r.render_no_game_yet())
        if session.is_finished:
            return Reply(
                r.render_already_finished(
                    session.puzzle_code, session.target_word
                )
            )

        async with session.lock:
            try:
                resp = await self.api.hint(session.game_id)
            except GameNotFound:
                # 重建 + 重放后再 hint
                new_gid, replayed = await self.api.rebuild_and_replay(
                    session.puzzle_code, session.history
                )
                session.replace_game(game_id=new_gid, history=replayed)
                resp = await self.api.hint(session.game_id)
            except ApiError as e:
                if e.status in (400, 409, 429):
                    return Reply(r.render_error(e.detail))
                raise

            record = resp["record"]
            session.append_history(record)
            session.hint_ever_used = True

            md = r.render_hint_response(
                hint_record=record,
                history=session.history,
                puzzle_code=session.puzzle_code,
                target_length=session.target_length,
                extra_hint_count=resp.get("extra_hint_count", 0),
                extra_hint_limit=resp.get("extra_hint_limit", 0),
                top_n=self.config.history_top_n,
            )
            return Reply(md)

    async def _cmd_giveup(self, conversation_id: str) -> Reply:
        session = self.sessions.get(conversation_id)
        if not session:
            return Reply(r.render_no_game_yet())
        if session.is_finished:
            return Reply(
                r.render_already_finished(
                    session.puzzle_code, session.target_word
                )
            )

        async with session.lock:
            try:
                summary = await self.api.give_up(session.game_id)
            except GameNotFound:
                # 重建后立即 giveup（不需要重放，反正要看答案）
                created = await self.api.by_code(session.puzzle_code)
                session.replace_game(
                    game_id=created["game_id"], history=[]
                )
                summary = await self.api.give_up(session.game_id)
            target = summary.get("target") or "（未知）"
            session.is_finished = True
            session.give_up_ever = True
            session.target_word = target
            # 用后端返回的最终 history 替换本地（更权威）
            session.history = list(summary.get("history") or session.history)
            md = r.render_give_up(
                target_word=target,
                puzzle_code=session.puzzle_code,
                history=session.history,
            )
            share_url = self.config.share_url(session.puzzle_code)
            return Reply(md, keyboard=kb.end_game_keyboard(share_url))

    async def _cmd_encode(self, word_arg: str) -> Reply:
        word = word_arg.strip()
        if not word:
            return Reply(
                r.render_error("用法：`/出题 你想作为答案的词`，例如 `/出题 苹果`")
            )
        if len(word) > 8 or not _CN_RE.match(word):
            return Reply(
                r.render_error("出题词请为 1-8 个汉字。")
            )
        try:
            data = await self.api.encode_puzzle(word)
        except PuzzleNotEncodable:
            return Reply(
                r.render_error("不支持创建该词语，请更换词语重新尝试。")
            )
        except ApiError as e:
            return Reply(
                r.render_error(f"出题失败：{e.detail}")
            )

        share_url = self.config.share_url(data["puzzle_code"])
        md = r.render_custom_puzzle(
            word=data["word"],
            puzzle_code=data["puzzle_code"],
            target_length=data["target_length"],
            share_url=share_url,
        )
        return Reply(
            md,
            keyboard=kb.custom_puzzle_keyboard(share_url, data["puzzle_code"]),
        )
