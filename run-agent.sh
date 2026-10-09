#!/usr/bin/env bash
# Agent 对战开发版启动脚本
#   ./run-agent.sh start|stop|restart|status|log
# 后端 :8001，前端 :5174（与正式版 :8000/:5173 隔离，互不影响）
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_PORT="${SEMANTLE_PORT:-8001}"
FRONTEND_PORT="${SEMANTLE_WEB_PORT:-5174}"
# 复用 chinese-semantle 的 venv（依赖一致：fastapi/uvicorn/gensim/httpx）
VENV="${SEMANTLE_VENV:-$HOME/workspace/categories/artifacts/chinese-semantle/backend/.venv}"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"

is_up() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$1$"; }

start() {
  [ -f "$DIR/.env" ] || { echo "缺少 .env（需要 DEEPSEEK_API_KEY）"; exit 1; }
  is_up "$BACKEND_PORT" && echo "backend 已在 :$BACKEND_PORT 运行" || {
    ( cd "$DIR/backend" && set -a && . "$DIR/.env" && set +a && \
      setsid "$VENV/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
      > "$LOG_DIR/backend.log" 2>&1 < /dev/null & )
    echo "backend 启动中 -> http://127.0.0.1:$BACKEND_PORT"
  }
  is_up "$FRONTEND_PORT" && echo "frontend 已在 :$FRONTEND_PORT 运行" || {
    setsid python3 -m http.server "$FRONTEND_PORT" --bind 127.0.0.1 --directory "$DIR/frontend" \
      > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
    echo "frontend 启动中 -> http://127.0.0.1:$FRONTEND_PORT"
  }
  echo "对战页面：http://127.0.0.1:$FRONTEND_PORT/race.html"
}

stop() {
  pkill -f "uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT" 2>/dev/null || true
  pkill -f "http.server $FRONTEND_PORT" 2>/dev/null || true
  echo "已停止"
}

status() {
  is_up "$BACKEND_PORT" && echo "backend  :$BACKEND_PORT  UP" || echo "backend  :$BACKEND_PORT  DOWN"
  is_up "$FRONTEND_PORT" && echo "frontend :$FRONTEND_PORT  UP" || echo "frontend :$FRONTEND_PORT  DOWN"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  log) tail -f "$LOG_DIR/backend.log" ;;
  *) echo "用法: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
