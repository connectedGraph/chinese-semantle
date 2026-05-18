"""单实例锁：用 pidfile + fcntl 文件锁，防止多个 bot 进程同时连 QQ 网关。

多实例同时跑会导致：
1. 多个进程收到同一条用户消息，竞争 msg_seq=1，触发 40054005 去重错误
2. 用户客户端看到的回复来自不同进程，puzzle_code 等状态错乱
3. 每月 4 条主动消息额度被瓜分

使用：
    with single_instance_guard():
        run_bot()  # 持有锁的代码
"""
from __future__ import annotations

import fcntl
import logging
import os
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LOCK_PATH = Path(__file__).resolve().parent / ".bot.lock"


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance_guard(lock_path: Path | str = DEFAULT_LOCK_PATH):
    """阻止多实例同时启动。

    用 ``fcntl.LOCK_EX | LOCK_NB`` 拿独占锁；拿不到说明已有实例。
    异常退出时锁会随 fd 关闭自动释放。
    """
    path = Path(lock_path)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            # 读出 pidfile 里的旧 pid 给出友好提示
            try:
                with open(path, "r", encoding="utf-8") as f:
                    other_pid = f.read().strip()
            except Exception:
                other_pid = "?"
            raise AlreadyRunning(
                f"已有 bot 进程在跑（PID={other_pid}），请先 `kill {other_pid}` 或："
                f"\n  pkill -9 -f 'qqbot.bot'"
                f"\n再启动新进程，避免多实例竞争触发 QQ 消息去重（40054005）。"
            ) from e

        # 写入当前 PID（供其他进程查看）
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        # 进程退出后的兜底清理（不强制要求，下次启动还会原子重写）
        try:
            if path.exists():
                # 不删除文件本身，只清空内容（避免重启窗口期文件被误用）
                path.write_text("", encoding="utf-8")
        except OSError:
            pass
