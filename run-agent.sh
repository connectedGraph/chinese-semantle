#!/usr/bin/env bash
# Agent 对战启动脚本（前端 = React + TS + Tailwind 工程，位于 web/）
#   ./run-agent.sh start|stop|restart|status|log|build
#   start  后端 :8001，前端 :5174（与正式版 :8000/:5173 隔离，互不影响）
#   build  重新构建前端（web/dist）
#   dev    前端 Vite 开发服务器 :5175（热更新，/api 代理到 :8001）
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
    if [ ! -f "$DIR/web/dist/index.html" ]; then build_web; fi
    setsid python3 -m http.server "$FRONTEND_PORT" --bind 127.0.0.1 --directory "$DIR/web/dist" \
      > "$LOG_DIR/frontend.log" 2>&1 < /dev/null &
    echo "frontend 启动中 -> http://127.0.0.1:$FRONTEND_PORT"
  }
  echo "对战页面：http://127.0.0.1:$FRONTEND_PORT/"
}

build_web() {
  echo "构建前端 web/ ..."
  ( cd "$DIR/web" && [ -d node_modules ] || npm install; npm run build )
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

dev() {
  ( cd "$DIR/web" && [ -d node_modules ] || npm install; npm run dev )
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  build) build_web ;;
  dev) dev ;;
  log) tail -f "$LOG_DIR/backend.log" ;;
  *) echo "用法: $0 {start|stop|restart|status|build|dev|log}"; exit 1 ;;
esac
