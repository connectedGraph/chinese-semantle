# 部署指南

本文档介绍如何在你自己的 VPS 上部署一份猜词游戏实例。

> 这是一份通用部署样本。文中所有 `<your-domain>` / `<your-server-ip>` 等都是占位符，请按你的实际情况替换。

## 目标架构

```
                        ┌──────────────────────────────────────┐
                        │  Linux VPS (Ubuntu 22.04+ / 2C2G+)   │
                        │                                      │
   <your-domain>  ──────┤  Caddy (443/80) ─┬─ /api/* ─→ backend:8000 (FastAPI + LightEngine)
                        │                  ├─ /docs/  ─→ backend:8000
                        │                  └─ /       ─→ frontend (静态)
                        │                                      │
                        │  qqbot (可选) ──────WSS出站─────→ QQ 开放平台
                        └──────────────────────────────────────┘
                                          ↓
                                  Postgres（推荐 Neon）
```

容器：3 个独立容器（caddy + backend + qqbot）共享同一个 docker network。

## 前置条件

- 一台 Linux VPS（Ubuntu 22.04 / 24.04 LTS 推荐），≥ 2 核 2GB 内存
- 一个域名，已通过 DNS A 记录指向 VPS 公网 IP
- 已安装 Docker & Docker Compose v2
- 一个 Postgres 数据库（推荐 [Neon](https://neon.tech) 免费档，或自建）
- 已生成预计算数据 `backend/data/precomputed/neighbors.sqlite`（详见 [README.md](./README.md) 第 4 步）

## Step 1：克隆仓库

```bash
git clone <你的仓库地址> ~/projects/chinese-semantle
cd ~/projects/chinese-semantle
```

## Step 2：准备每日挑战配置

仓库里只提供示例，需要复制并填入真实排期：

```bash
cp backend/data/daily_challenges.example.yaml backend/data/daily_challenges.yaml
vim backend/data/daily_challenges.yaml
```

> `daily_challenges.yaml` 含未来谜底，已在 `.gitignore` 中，不会被推回 Git。
> 不配也行——未配置的日期会自动从 `daily_pool.txt` 按 HMAC 抽签。

## Step 3：写 .env

```bash
cp .env.example .env
vim .env
```

至少需要这些变量：

```bash
# 必填
PUZZLE_SECRET=<随机 32+ 位字符串，用 `openssl rand -base64 32` 生成>
DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require

# 可选：QQ 机器人（不部署 bot 可不填）
QQBOT_APPID=<你的 QQ 机器人 AppID>
QQBOT_APPSECRET=<你的 QQ 机器人 AppSecret>
```

> ⚠️ `PUZZLE_SECRET` 必须与本地 `build_precomputed` 时使用的 secret **完全一致**，否则 sqlite 里所有谜底编号都会失配。

## Step 4：起反代容器（Caddy）

把 Caddy 单独放一个目录，便于以后接管多个项目共用 80/443：

```bash
mkdir -p ~/projects/caddy && cd ~/projects/caddy
docker network create web
```

新建 `~/projects/caddy/Caddyfile`：

```caddy
<your-domain> {
    reverse_proxy /api/*  semantle-backend:8000
    reverse_proxy /docs/* semantle-backend:8000
    reverse_proxy /openapi.json semantle-backend:8000

    handle {
        root * /srv/semantle-frontend
        file_server
    }
}
```

新建 `~/projects/caddy/docker-compose.yml`：

```yaml
services:
  caddy:
    image: caddy:2-alpine
    container_name: caddy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - /srv/semantle-frontend:/srv/semantle-frontend:ro
      - caddy_data:/data
      - caddy_config:/config
    networks:
      - web

volumes:
  caddy_data:
  caddy_config:

networks:
  web:
    external: true
```

启动：

```bash
docker compose up -d
```

## Step 5：同步前端 + 起 backend / qqbot

回到游戏目录，初始化前端目录并起容器：

```bash
cd ~/projects/chinese-semantle
sudo mkdir -p /srv/semantle-frontend
sudo rsync -a --delete frontend/ /srv/semantle-frontend/

docker compose up -d --build
```

容器拓扑（来自仓库根目录的 `docker-compose.yml`）：

- `semantle-backend`：FastAPI + LightEngine，仅暴露在 docker 内网
- `semantle-qqbot`：QQ 机器人长连接，仅出站，不开端口

## Step 6：验证

```bash
# 健康检查
curl -fsS https://<your-domain>/api/health
# 期望 {"ok":true,"vocab_size":...}

# 容器状态
docker compose ps
# 期望 backend healthy、qqbot Up（如果配了 QQBOT_APPID）
```

打开浏览器访问 `https://<your-domain>/` 即可。

---

## 日常运维参考

### 更新代码

```bash
cd ~/projects/chinese-semantle
git pull
sudo rsync -a --delete frontend/ /srv/semantle-frontend/
docker compose build
docker compose up -d
```

> 也可以写成一个 `deploy.sh` 脚本，一键完成上述步骤。

### 查看日志

```bash
docker logs semantle-backend --tail 100 -f
docker logs semantle-qqbot   --tail 100 -f
docker logs caddy            --tail 100 -f
```

### 改 .env 后重启

```bash
docker compose up -d --force-recreate   # 不能用 restart，restart 不重读 .env
```

### 加新域名 / 新项目共用 Caddy

```bash
vim ~/projects/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile   # 热重载，不掉连接
```

---

## 故障排查

### 容器启动后立刻 OOM 重启

LightEngine 启动会一次性把全部谜底邻居解压进内存（~1 GB）。如果机器只有 1 GB RAM，会被 OOM。

- 调高 `docker-compose.yml` 里的 `memory:` 限制
- 或开 swap：`sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
- 或换更小的引擎（参考 `engine.py` 的 LocalEngine，~500MB）

### Caddy 签证书失败

```bash
docker logs caddy --tail 50 | grep -i 'error\|challenge'
```

常见原因：
- DNS A 记录还没生效（`dig +short <your-domain>` 验证）
- CDN 代理（如 Cloudflare 小黄云）开了 → **必须关掉**，让 Caddy 直接拿到 80/443 流量
- VPS 防火墙没开 80/443

### `/api/health` 返回 500 或连不上数据库

- 检查 `.env` 里 `DATABASE_URL` 可达：`docker exec semantle-backend python -c "import os, asyncpg, asyncio; asyncio.run(asyncpg.connect(os.environ['DATABASE_URL']).close())"`
- 用 Pooled connection（连接串带 `-pooler`），并把 VPS region 选在数据库附近

### 玩游戏所有词都显示「远」/ proximity_rank 全 null

构建 `neighbors.sqlite` 时用的 `PUZZLE_SECRET` ≠ `.env` 里的 secret。本地用线上同一份 secret 重新跑：

```bash
cd backend
PUZZLE_SECRET="<生产 secret>" python -m scripts.build_precomputed --rebuild
git add backend/data/precomputed/ && git commit -m "rebuild precomputed with prod secret" && git push
```

服务器侧重新 `git pull && docker compose up -d --build`。

### QQ 机器人 `/改名` 报 `[Errno 16] Device or resource busy`

如果你 fork 时改过 `docker-compose.yml`：必须把昵称数据**挂目录**而不是单文件——
单文件 bind mount 在容器里是独立挂载点，不允许 `rename(2)` 替换（EBUSY）。
正确写法见仓库默认配置：

```yaml
volumes:
  - ./qqbot/data:/app/qqbot/data        # ✅ 目录
  # - ./qqbot/nicknames.json:/app/qqbot/nicknames.json   # ❌ 单文件
```
