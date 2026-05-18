"""Markdown 文本渲染（GFM 表格版）。

设计原则：
- 仅在顶层用 markdown 标题（无 H1/H2 嵌入正文）。
- 「🆕 本次猜词」与「📋 猜词记录」用粗体行 + 表格清晰分块。
- 进度条 5 格制，避免手机端换行。
- cold 一律展示"低"，不展示数值（后端 -100 是占位）。
- is_target 时按"满级 100.00 + 5 红格 + 🎯 命中"渲染。

色块映射：hot 🔴 / warm 🟠 / cold ⚪️
"""
from __future__ import annotations

# ---------- 常量 ----------

_LEVEL_FILLED = {"hot": "🔴", "warm": "🟠"}
_EMPTY = "⚪️"
_BAR_LEN = 5
_DIVIDER = "---"  # GFM 水平分隔线（QQ 客户端会渲染为细横线）


# ---------- 单条记录渲染 ----------

def _bar(record: dict) -> str:
    if record.get("is_target"):
        return _LEVEL_FILLED["hot"] * _BAR_LEN
    level = record.get("proximity_level", "cold")
    if level == "cold":
        return _EMPTY * _BAR_LEN
    pct = record.get("similarity_pct") or 0.0
    # 5 格制：1 格 = 20 分
    filled = max(1, min(_BAR_LEN, round(pct / 20)))
    return _LEVEL_FILLED[level] * filled + _EMPTY * (_BAR_LEN - filled)


def _player_label(record: dict) -> str:
    if record.get("is_hint"):
        return "系统提示"
    return record.get("player_name") or "匿名"


def _sim_cell(record: dict) -> str:
    """相似度 + 进度条一格内联展示。"""
    bar = _bar(record)
    if record.get("is_target"):
        return f"100.00 {bar}"
    if record.get("proximity_level", "cold") == "cold":
        return f"低 {bar}"
    pct = record.get("similarity_pct") or 0.0
    return f"{pct:.2f} {bar}"


def _rank_cell(record: dict) -> str:
    if record.get("is_target"):
        return "🎯 命中"
    if record.get("proximity_level", "cold") == "cold":
        return "—"
    rank = record.get("proximity_rank") or 0
    return f"#{rank}" if rank > 0 else "—"


def _word_cell(record: dict) -> str:
    """词列：始终加粗；命中/提示带 emoji 标记。"""
    word = record.get("word", "")
    tag = ""
    if record.get("is_target"):
        tag = " 🎯"
    elif record.get("is_hint"):
        tag = " 💡"
    return f"**{word}**{tag}"


def _table_header() -> list[str]:
    # 列顺序：词 | 相似度 | 排名 | 玩家 | 次序
    return [
        "| 词 | 相似度 | 排名 | 玩家 | 次序 |",
        "|---|---|---|---|---|",
    ]


def _table_row(record: dict) -> str:
    return (
        f"| {_word_cell(record)} "
        f"| {_sim_cell(record)} "
        f"| {_rank_cell(record)} "
        f"| {_player_label(record)} "
        f"| #{record.get('order', '?')} |"
    )


# ---------- 列表分块 ----------

def _split_latest_and_others(history: list[dict]) -> tuple[dict | None, list[dict]]:
    """按 order 升序，最后一条作为 latest，其余按相似度降序（cold 垫底）。"""
    if not history:
        return None, []
    by_order = sorted(history, key=lambda r: r.get("order", 0))
    latest = by_order[-1]
    others = by_order[:-1]

    def sort_key(r: dict):
        level = r.get("proximity_level", "cold")
        pct = r.get("similarity_pct") or 0.0
        return (1 if level == "cold" else 0, -pct, -r.get("order", 0))

    return latest, sorted(others, key=sort_key)


def render_history_block(
    history: list[dict],
    *,
    top_n: int = 10,
    show_all: bool = False,
) -> str:
    """渲染'本次 + 历史'。"""
    if not history:
        return "_暂无猜词，@ 我直接发汉字开始猜～_"

    latest, others = _split_latest_and_others(history)
    if not show_all:
        others = others[:top_n]

    lines: list[str] = [_DIVIDER, "**🆕 本次猜词**"]
    if latest is not None:
        lines.extend(_table_header())
        lines.append(_table_row(latest))

    if others:
        lines.append("")
        lines.append(_DIVIDER)
        lines.append("**📋 猜词记录**（按相似度排序）")
        lines.extend(_table_header())
        for rec in others:
            lines.append(_table_row(rec))

    return "\n".join(lines)


# ---------- 顶层模板 ----------

def render_game_created(
    *,
    puzzle_code: str,
    target_length: int,
    source: str,
    daily_date: str | None = None,
) -> str:
    icon_map = {"random": "🎲", "daily": "📅", "shared": "🎮"}
    label_map = {
        "random": "新游戏",
        "daily": f"每日挑战（{daily_date or '今日'}）",
        "shared": "指定游戏",
    }
    icon = icon_map.get(source, "🎮")
    label = label_map.get(source, "新游戏")

    return (
        f"{icon} **{label}** · 编号 `#{puzzle_code}` · 答案 **{target_length} 字**\n"
        f"@ 我并发汉字（1-8 字）即可猜词。\n"
        f"_可用命令：`/提示`（每局上限 5 次）、`/放弃`（揭晓答案）_"
    )


def _header_line(
    *,
    puzzle_code: str,
    target_length: int,
    guess_count: int,
) -> str:
    return (
        f"`#{puzzle_code}` · 答案 **{target_length} 字** · "
        f"已猜 **{guess_count}** 次"
    )


def render_guess_response(
    *,
    record: dict,
    history: list[dict],
    puzzle_code: str,
    is_finished: bool,
    target_length: int,
    top_n: int,
) -> str:
    if is_finished and record.get("is_target"):
        body = render_history_block(history, top_n=top_n, show_all=True)
        return (
            f"🎉 **猜中了！答案是 {record.get('word')}** "
            f"· `#{puzzle_code}` · 共 **{len(history)}** 次\n\n"
            f"{body}\n\n"
            f"_点击下方按钮再来一局或分享给朋友～_"
        )

    head = _header_line(
        puzzle_code=puzzle_code,
        target_length=target_length,
        guess_count=len(history),
    )
    body = render_history_block(history, top_n=top_n, show_all=False)
    return f"{head}\n\n{body}"


def render_give_up(
    *, target_word: str, puzzle_code: str, history: list[dict]
) -> str:
    body = render_history_block(history, show_all=True)
    return (
        f"🏳️ **已放弃，答案是 {target_word}** · `#{puzzle_code}` "
        f"· 共 **{len(history)}** 次\n\n"
        f"{body}"
    )


def render_hint_response(
    *,
    hint_record: dict,
    history: list[dict],
    puzzle_code: str,
    target_length: int,
    extra_hint_count: int,
    extra_hint_limit: int,
    top_n: int,
) -> str:
    """与 guess 响应**结构一致**：先头部，再表格分块。

    提示词单独在顶部用粗体行展示，但**不再单独渲染一个表格**——
    它已经作为 `latest` 出现在「🆕 本次猜词」表里（is_hint=True 会带 💡 标记）。
    """
    head = _header_line(
        puzzle_code=puzzle_code,
        target_length=target_length,
        guess_count=len(history),
    )
    hint_word = hint_record.get("word", "")
    body = render_history_block(history, top_n=top_n, show_all=False)
    return (
        f"💡 **提示来啦：{hint_word}** · 已用 {extra_hint_count}/{extra_hint_limit}\n"
        f"{head}\n\n{body}"
    )


def render_custom_puzzle(
    *, word: str, puzzle_code: str, target_length: int, share_url: str
) -> str:
    return (
        f"✅ **出题成功**\n"
        f"答案：**{word}**（{target_length} 字）\n"
        f"分享编号：`#{puzzle_code}`\n"
        f"分享链接：{share_url}\n\n"
        f"_把链接发给朋友，或在群里 `@bot /指定游戏 {puzzle_code}` 直接开局～_"
    )


def render_help() -> str:
    return (
        "🎮 **中文猜词机器人指令**\n\n"
        "**群聊**\n"
        "- `/新游戏` 或 `/随机计分游戏` —— 开一局随机谜底\n"
        "- `/每日挑战` —— 开始今日的每日挑战\n"
        "- `/指定游戏 编号或链接` —— 例：`/指定游戏 QT5PWS`\n"
        "- `/提示` —— 获取一个提示词（每局上限 5 次）\n"
        "- `/放弃` —— 揭晓答案并结束本局\n"
        "- 直接发汉字（1-8 字）即为猜词\n\n"
        "**私聊**\n"
        "- `/出题 苹果` —— 创建以「苹果」为答案的自定义局\n\n"
        "**通用**\n"
        "- `/改名 太空小孩` —— 设置你在 bot 内的显示昵称\n\n"
        "_QQ 机器人模式不参与网页排行榜，仅供娱乐～_"
    )


def render_error(msg: str) -> str:
    return f"⚠️ {msg}"


def render_no_game_yet() -> str:
    return (
        "本群还没有进行中的游戏。@ 我并发送 `/新游戏`、`/每日挑战` "
        "或 `/指定游戏 编号` 开始吧～"
    )


def render_already_finished(puzzle_code: str, target: str | None) -> str:
    line_target = f"答案是 **{target}**。" if target else ""
    return (
        f"本局 `#{puzzle_code}` 已经结束。{line_target}\n"
        f"@ 我并发送 `/新游戏` 再来一局～"
    )


def render_nickname_set(nickname: str) -> str:
    return f"👤 已记住你的昵称 **{nickname}**，之后猜词记录都会用这个名字。"


def render_nickname_cleared() -> str:
    return "👤 已清除你的自定义昵称。"

