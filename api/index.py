"""
Vercel serverless function 入口。

Vercel 的 Python runtime 做静态扫描，要求文件顶层有名为 `app` 的对象。
所以我们：
1. 先在顶层无条件创建一个占位 FastAPI（保证静态扫描能找到 `app`）
2. 然后尝试 import 真正的 app.main:app，成功就用它的路由替换占位
3. 失败时占位 app 仍然能起来，并返回结构化诊断 JSON

这样无论 import 成功失败，Vercel function 都能启动，
不会再出现 FUNCTION_INVOCATION_FAILED。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vercel.entry")

# Vercel 上默认走 LightEngine（不加载 1GB 词向量）
os.environ.setdefault("SEMANTLE_ENGINE", "light")

# 把 backend/ 加入 sys.path
# Vercel @vercel/python 把 function 解压到 /var/task，
# __file__ = /var/task/api/index.py，因此 parent.parent = /var/task
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

logger.info(
    "Vercel entry: ROOT=%s, BACKEND=%s, BACKEND.exists=%s",
    ROOT, BACKEND, BACKEND.exists(),
)

# ---- 顶层 app（Vercel 静态扫描要看到这个名字）----
# 先创建占位，import 成功后再替换为真实 app
app: FastAPI = FastAPI(title="Chinese Semantle (loading…)")

try:
    from app.main import app as _real_app  # noqa: E402
    app = _real_app
    logger.info("FastAPI app imported successfully.")
except Exception as e:  # noqa: BLE001
    # 任何 import 错误都 fallback 到诊断模式：
    # 不让 Vercel function 静默崩溃，而是把错误以 JSON 返回
    err_trace = traceback.format_exc()
    logger.error("FATAL: failed to import app.main:app\n%s", err_trace)

    _err_msg = str(e)
    _err_trace = err_trace
    _root_str = str(ROOT)
    _backend_str = str(BACKEND)
    _backend_exists = BACKEND.exists()
    _backend_listing: list[str] = []
    if _backend_exists:
        try:
            _backend_listing = sorted(p.name for p in BACKEND.iterdir())
        except Exception:  # noqa: BLE001
            _backend_listing = ["<iterdir failed>"]

    @app.get("/api/health")
    @app.get("/")
    def _boot_failed_root():
        return _diagnostic_payload()

    @app.get("/api/{full_path:path}")
    def _boot_failed_any(full_path: str):
        return _diagnostic_payload()

    def _diagnostic_payload():
        return {
            "ok": False,
            "boot_error": _err_msg,
            "traceback": _err_trace.splitlines()[-20:],
            "diagnostics": {
                "root": _root_str,
                "backend": _backend_str,
                "backend_exists": _backend_exists,
                "backend_listing": _backend_listing,
                "sys_path": sys.path[:10],
                "cwd": os.getcwd(),
                "python_version": sys.version,
            },
        }
