"""QQ 群机器人：中文猜词游戏接入。

独立子项目，与 backend/ 完全解耦。运行入口：
    python -m qqbot.bot

通过 HTTP 调用现有线上后端（默认 https://semantle.spacekid.me），
不依赖 backend/ 任何模块，不影响 Vercel 部署。
"""

__version__ = "0.1.0"
