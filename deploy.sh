#!/usr/bin/env bash
# 服务器侧：一键拉新代码 + 重新构建 + 滚动重启
#
# 使用：
#   ssh ubuntu@vps "cd ~/projects/chinese-semantle && ./deploy.sh"
# 或在服务器上：
#   cd ~/projects/chinese-semantle && ./deploy.sh

set -euo pipefail

cd "$(dirname "$0")"

echo "==> [1/5] git pull"
git pull --ff-only

echo "==> [2/5] 同步前端到 /srv/semantle-frontend"
sudo rsync -a --delete frontend/ /srv/semantle-frontend/

echo "==> [3/5] 重新构建镜像"
docker compose build

echo "==> [4/5] 滚动重启容器"
docker compose up -d

echo "==> [5/5] 健康检查"
sleep 3
docker compose ps
echo
echo "Backend health:"
curl -fsS http://localhost:8000/api/health 2>&1 | head -5 || \
    docker exec semantle-backend curl -fsS http://localhost:8000/api/health || \
    echo "  (backend may need more time to warm up)"

echo
echo "完成 ✅"
echo "访问： https://semantle.spacekid.me"
