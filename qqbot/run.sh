#!/usr/bin/env bash
# QQ Bot 一键启动脚本
#
# 功能：
#   1. 杀掉残留 qqbot.bot 进程（避免多实例触发 QQ 消息去重）
#   2. cd 到仓库根（脚本支持从任意目录调用）
#   3. 自动激活虚拟环境（如果存在）；不存在则用系统 python
#   4. 注入 SSL_CERT_FILE（macOS Python 需要）
#   5. 启动 python -m qqbot.bot
#
# 用法：
#   ./qqbot/run.sh             # 前台运行，Ctrl+C 退出
#   bash qqbot/run.sh          # 同上
#
# 环境变量（可选，覆盖默认行为）：
#   QQBOT_SKIP_KILL=1          跳过 pkill 步骤（如果你确定没残留）
#   QQBOT_VENV=path/to/venv    指定虚拟环境路径（默认 .venv-qqbot）

set -euo pipefail

# ---- 颜色输出 ----
if [ -t 1 ]; then
    BLUE='\033[1;34m'; GREEN='\033[1;32m'; YELLOW='\033[1;33m'
    RED='\033[1;31m'; RESET='\033[0m'
else
    BLUE=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi

log()   { printf "${BLUE}[run.sh]${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}[run.sh]${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}[run.sh]${RESET} %s\n" "$*"; }
err()   { printf "${RED}[run.sh]${RESET} %s\n" "$*" >&2; }

# ---- 解析仓库根（脚本所在目录的上一级）----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
log "仓库根：$REPO_ROOT"

# ---- 1. 清理残留进程 ----
if [ "${QQBOT_SKIP_KILL:-0}" != "1" ]; then
    # 用 || true 防止 pgrep 找不到进程时退出码非 0 触发 set -e
    set +e
    EXISTING_PIDS=$(pgrep -f 'qqbot\.bot')
    set -e
    if [ -n "${EXISTING_PIDS:-}" ]; then
        warn "发现残留 qqbot.bot 进程：${EXISTING_PIDS//$'\n'/ }，正在清理..."
        # shellcheck disable=SC2086
        kill -9 ${EXISTING_PIDS} 2>/dev/null || true
        sleep 1
        ok "残留进程已清理"
    else
        log "无残留进程"
    fi
else
    log "QQBOT_SKIP_KILL=1，跳过 pkill 步骤"
fi

# ---- 2. 清理可能残留的锁文件（被强杀的进程不会自动清理）----
LOCK_FILE="$REPO_ROOT/qqbot/.bot.lock"
if [ -f "$LOCK_FILE" ]; then
    rm -f "$LOCK_FILE"
fi

# ---- 3. 激活虚拟环境 ----
VENV_PATH="${QQBOT_VENV:-$REPO_ROOT/.venv-qqbot}"
if [ -f "$VENV_PATH/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate"
    ok "已激活虚拟环境：$VENV_PATH"
else
    warn "未找到虚拟环境 $VENV_PATH，使用系统 python"
    warn "如需创建：python3 -m venv .venv-qqbot && source .venv-qqbot/bin/activate && pip install -r qqbot/requirements.txt"
fi

# ---- 4. 注入 SSL 证书（macOS Python 默认证书链不全）----
if python -c "import certifi" 2>/dev/null; then
    CERT_FILE=$(python -c 'import certifi; print(certifi.where())')
    export SSL_CERT_FILE="$CERT_FILE"
    log "SSL_CERT_FILE=$CERT_FILE"
else
    warn "未安装 certifi，可能在 macOS 上报 SSLCertVerificationError"
    warn "解决：pip install certifi"
fi

# ---- 5. 启动 ----
ok "启动 QQ 机器人……（Ctrl+C 退出）"
echo
exec python -m qqbot.bot
