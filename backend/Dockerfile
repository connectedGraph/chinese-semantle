# Chinese Semantle backend (LightEngine 模式)
#
# 设计要点：
# - 用 python:3.11-slim 而不是完整版，镜像小（最终 ~150MB）
# - 用 Vercel 那份精简 requirements.txt（只装 fastapi/pydantic/asyncpg/pyyaml）
#   不装 gensim/numpy，因为 LightEngine 不需要
# - 镜像里随包带上 backend/data/precomputed/neighbors.sqlite（78MB，必须有）
# - LightEngine 会以 ?mode=ro&immutable=1 打开 sqlite，容器内 /app 可读写也无所谓
# - 暴露 8000 端口，由 Caddy 反代到外网

FROM python:3.11-slim AS base

# 基础环境
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://mirrors.tencent.com/pypi/simple \
    SEMANTLE_ENGINE=light \
    PORT=8000

WORKDIR /app

# ---- 装系统依赖（curl 用于 healthcheck）----
# 用腾讯云 debian 镜像源加速（国外 deb.debian.org 在国内服务器慢得离谱）
RUN sed -i 's|http://deb.debian.org|http://mirrors.tencentyun.com|g; s|http://security.debian.org|http://mirrors.tencentyun.com|g' /etc/apt/sources.list.d/*.sources 2>/dev/null || true && \
    apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# ---- 装 Python 依赖（先复制 requirements.txt 单独一层，缓存友好）----
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir "uvicorn[standard]==0.31.0"

# ---- 复制后端代码 ----
# 注意：这个 Dockerfile 期望 build context = 项目根（不是 backend/）
# 因为 requirements.txt 在项目根
COPY backend/ /app/

# 健康检查（让 docker / compose 能感知到后端真活着）
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/api/health || exit 1

EXPOSE 8000

# 不用 --reload（生产不需要文件监听），不用 root（创个 app 用户）
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
