"""按钮 JSON 构造器。

QQ 群机器人 keyboard.content 结构：
    {"rows": [ {"buttons": [ {...}, {...} ]}, ... ]}
最多 5 行，每行最多 5 个按钮。

action.type 速查：
    0 = 跳转按钮（http/scheme，data 为 URL）
    1 = 回调按钮（点击触发 INTERACTION_CREATE，需服务端回应 code）
    2 = 指令按钮（点击后在输入框插入 @bot data 文本，可配 enter=true 自动发）
"""
from __future__ import annotations

from typing import Any


def _button(
    btn_id: str,
    label: str,
    visited_label: str,
    style: int,
    action: dict[str, Any],
) -> dict:
    return {
        "id": btn_id,
        "render_data": {
            "label": label,
            "visited_label": visited_label,
            "style": style,
        },
        "action": action,
    }


def link_button(
    btn_id: str, label: str, url: str, *, style: int = 0
) -> dict:
    """跳转按钮（type=0，无回调签名负担）。"""
    return _button(
        btn_id,
        label,
        label,
        style,
        {
            "type": 0,
            "permission": {"type": 2},  # 所有人可见
            "data": url,
            "unsupport_tips": "请升级 QQ 版本以使用按钮",
        },
    )


def command_button(
    btn_id: str,
    label: str,
    command: str,
    *,
    style: int = 1,
    enter: bool = False,
    reply: bool = False,
) -> dict:
    """指令按钮（type=2）。点击后在输入框插入 ``@bot {command}``。

    ``enter=True`` 仅在单聊生效；群聊里只能填充输入框由用户手动发出，
    但这已足够提供"再来一局"的快捷入口。
    """
    return _button(
        btn_id,
        label,
        label,
        style,
        {
            "type": 2,
            "permission": {"type": 2},
            "data": command,
            "enter": enter,
            "reply": reply,
            "unsupport_tips": "请升级 QQ 版本以使用按钮",
        },
    )


def keyboard_payload(rows: list[list[dict]]) -> dict:
    """把若干行按钮包装成 reply(keyboard=...) 期望的 dict。"""
    if len(rows) > 5:
        rows = rows[:5]
    cleaned = []
    for row in rows:
        buttons = [b for b in row if b]
        if not buttons:
            continue
        if len(buttons) > 5:
            buttons = buttons[:5]
        cleaned.append({"buttons": buttons})
    return {"content": {"rows": cleaned}}


# ---------- 业务级快捷构造 ----------

def end_game_keyboard(share_url: str) -> dict:
    """终局按钮：复制分享链接 + 再来一局。"""
    return keyboard_payload([
        [
            link_button("share", "复制分享链接", share_url, style=0),
            command_button(
                "again", "再来一局", "/新游戏", style=1
            ),
        ],
    ])


def custom_puzzle_keyboard(share_url: str, puzzle_code: str) -> dict:
    """私聊出题成功后的按钮：打开链接 + 在群里发起这局。"""
    return keyboard_payload([
        [
            link_button("open", "打开链接", share_url, style=0),
            command_button(
                "share_to_group",
                "用此谜底开局",
                f"/指定游戏 {puzzle_code}",
                style=1,
            ),
        ],
    ])
