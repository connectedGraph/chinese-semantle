#!/usr/bin/env bash
# 启动后端 API 服务
set -e

cd "$(dirname "$0")"

# 进入 venv（如已激活则跳过）
if [ -z "$VIRTUAL_ENV" ] && [ -d ".venv" ]; then
  source .venv/bin/activate
fi

# 默认端口 8000，可通过环境变量覆盖
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

echo "Starting Chinese Semantle API on http://${HOST}:${PORT}"
exec uvicorn app.main:app --host "${HOST}" --port "${PORT}" --reload
