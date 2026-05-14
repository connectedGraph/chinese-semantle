"""
Vercel serverless function 入口。

Vercel 的 Python runtime 会自动检测同名/同目录的 `app: FastAPI`，
按 ASGI 协议执行，无需 Mangum 等适配器。

这个文件做这几件事：
1. 把仓库根的 backend/ 加入 sys.path，让 `from app.main import app` 能找到
2. 强制走 LightEngine（不加载词向量）
3. 任何 import 失败时，仍然 export 一个最小可用的 FastAPI app，
   这样 Vercel 不会直接 FUNCTION_INVOCATION_FAILED，
   错误会以 JSON 形式返回，便于排查。
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vercel.entry")

# Vercel 上默认走 LightEngine（不加载 1GB 词向量）
os.environ.setdefault("SEMANTLE_ENGINE", "light")

# 把 backend/ 加入 sys.path
# Vercel @vercel/python 的 working dir 是 /var/task，
# __file__ = /var/task/api/index.py，因此 parent.parent = /var/task
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

logger.info("Vercel entry: ROOT=%s, BACKEND=%s, BACKEND.exists=%s",
            ROOT, BACKEND, BACKEND.exists())

try:
    from app.main import app  # noqa: E402,F401  re-export for Vercel
    logger.info("FastAPI app imported successfully.")
except Exception as e:  # noqa: BLE001
    # 关键：任何 import 错误都 fallback 到一个最小 app，
    # 这样能让 Vercel function 起来，把错误以 JSON 返回，便于诊断
    err_trace = traceback.format_exc()
    logger.error("FATAL: failed to import app.main:app\n%s", err_trace)

    from fastapi import FastAPI

    app = FastAPI(title="Chinese Semantle (BOOT FAILED)")

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
    @app.get("/api/{full_path:path}")
    @app.get("/")
    def _boot_failed(full_path: str = ""):
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
