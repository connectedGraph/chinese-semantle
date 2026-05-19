"""昵称持久化：``user_openid`` / ``member_openid`` -> nickname。

由于 QQ 群机器人 v2 API 不下发用户真实昵称（隐私设计），
我们提供 ``/改名 你的昵称`` 命令让用户自助绑定。

存储为 JSON 文件，进程重启后仍可用。线程安全：用 asyncio.Lock 串行写。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_PATH = _DATA_DIR / "nicknames.json"

# 兼容旧路径：早期版本把 nicknames.json 直接放在 qqbot/ 下，
# 通过 docker-compose 单文件 bind mount 进容器。
# 单文件挂载点不允许 rename(2) 替换（EBUSY），现已改为挂载 data/ 目录。
_LEGACY_PATH = Path(__file__).resolve().parent / "nicknames.json"


class NicknameStore:
    """openid -> nickname 持久化映射。

    支持的 openid 来源：
    - 私聊：``message.author.user_openid``
    - 群聊：``message.author.member_openid``（群+用户维度）

    昵称限制：1-12 字符，去前后空白；过长截断。
    """

    MAX_LEN = 12

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self._path = Path(path)
        self._data: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        # 一次性迁移：旧版本把文件放在 qqbot/nicknames.json，
        # 现在挂载点是 qqbot/data/，把旧数据搬过来（仅当新位置不存在时）。
        if (
            self._path == DEFAULT_PATH
            and not self._path.exists()
            and _LEGACY_PATH.exists()
        ):
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                _LEGACY_PATH.replace(self._path)
                logger.info("migrated legacy nicknames.json → %s", self._path)
            except Exception as e:  # pragma: no cover
                logger.warning("legacy nickname migration failed: %s", e)

        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data = {str(k): str(v) for k, v in raw.items() if v}
                logger.info("nickname store loaded: %d entries", len(self._data))
        except Exception as e:  # pragma: no cover
            logger.warning("nickname store load failed: %s", e)

    async def _save(self) -> None:
        # 原子写：先写到临时文件再替换
        tmp_dir = self._path.parent
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".nicknames-", dir=tmp_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def get(self, openid: str | None) -> str | None:
        if not openid:
            return None
        return self._data.get(openid)

    async def set(self, openid: str, nickname: str) -> str:
        nickname = nickname.strip()
        if len(nickname) > self.MAX_LEN:
            nickname = nickname[: self.MAX_LEN]
        async with self._lock:
            self._data[openid] = nickname
            await self._save()
        return nickname

    async def clear(self, openid: str) -> None:
        async with self._lock:
            if openid in self._data:
                del self._data[openid]
                await self._save()

    def fallback_name(self, openid: str | None, *, scope: str = "user") -> str:
        """没有自定义昵称时的兜底显示名（取 openid 后 6 位）。"""
        if openid:
            short = str(openid)[-6:]
            prefix = "群友" if scope == "group" else "用户"
            return f"{prefix}_{short}"
        return "群友" if scope == "group" else "用户"
