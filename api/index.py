"""
Vercel serverless function 入口。

Vercel 的 Python runtime 会自动检测同名/同目录的 `app: FastAPI`，
按 ASGI 协议执行，无需 Mangum 等适配器。

这个文件只做两件事：
1. 把仓库根的 backend/ 加入 sys.path，让 `from app.main import app` 能找到
2. 把 FastAPI 实例重新 export 出来
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 backend/ 加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Vercel 上默认走 LightEngine（不加载 1GB 词向量）
os.environ.setdefault("SEMANTLE_ENGINE", "light")

from app.main import app  # noqa: E402,F401  re-export for Vercel
