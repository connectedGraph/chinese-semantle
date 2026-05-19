# VPS 部署指南（腾讯云轻量 + Docker + Caddy）

> 当前生产环境部署文档。Vercel 历史方案见 `DEPLOY.md`。
> 本文档假定你已有一台 Ubuntu 24.04 LTS 的服务器和一个域名。

## 当前生产架构

```
                          ┌──────────────────────────────────────┐
                          │  腾讯云轻量 (2C2G, Ubuntu 24.04)     │
                          │  IP: 81.71.130.80                    │
                          │                                      │
   semantle.spacekid.me ──┤  Caddy (443/80) ──┬─ /api/* ──→ backend:8000 (FastAPI + LightEngine)
                          │                   ├─ /docs/   ──→ backend:8000
   QQ 用户 @小Q猜词 ────────┤                   └─ /        ──→ /srv/semantle-frontend (静态)
        │                 │                                      │
        ▼                 │  semantle-qqbot ──────WSS出站──────→ QQ 开放平台
   QQ 开放平台             │                                      │
        ▲                 │                                      │
        │                 │  semantle-qqbot → http://backend:8000 (容器内网)
        └──── botpy ──────┤                                      │
                          └──────────────────────────────────────┘
                                              ↓
                                      Neon Postgres (云端)
```

容器拓扑：3 个独立容器全部加入 `web` docker network。
- `caddy`：~/projects/caddy/ (独立 compose，全局反代)
- `semantle-backend` + `semantle-qqbot`：~/projects/chinese-semantle/

## 资源占用基线

| 服务 | 内存（实际） | 内存（限制） | CPU 闲时 |
|---|---|---|---|
| caddy | ~12 MiB | 128 MiB | <1% |
| semantle-backend | ~1 GiB | 1200 MiB | <1% |
| semantle-qqbot | ~35 MiB | 256 MiB | <1% |
| **合计** | **~1.05 GiB** | — | — |

**主机剩余可用内存约 350 MiB + 1.9 GB swap**。如果未来要跑别的项目，建议每个服务限制 < 200 MiB，最多再跑 1-2 个。

LightEngine 的 ~1GB 是 **全部 1781 个 puzzle 邻居字典的解压缓存**，不是 page cache。如未来内存紧张，可考虑：
- 改用懒加载（按需解压 + LRU 淘汰）
- 或者直接简化为 LocalEngine（116MB 词向量，启动后 ~500MB）

## 日常运维速查

### 部署/更新

```bash
# 1. 在本机改完代码，git push 到 GitHub
git push

# 2. SSH 到服务器，跑一键部署
ssh ubuntu@81.71.130.80
cd ~/projects/chinese-semantle
./deploy.sh
```

### 查看服务状态

```bash
# 所有容器
docker ps

# 某个项目
cd ~/projects/chinese-semantle && docker compose ps

# 资源占用
docker stats --no-stream
```

### 查看日志

```bash
# 后端
docker logs semantle-backend --tail 100 -f

# Bot
docker logs semantle-qqbot --tail 100 -f

# Caddy（访问日志 + 错误日志）
docker logs caddy --tail 100 -f
# 详细访问日志（按域名分文件）
sudo tail -f ~/projects/caddy/logs/semantle.log
```

### 重启单个服务

```bash
cd ~/projects/chinese-semantle
docker compose restart backend         # 仅重启后端
docker compose restart qqbot           # 仅重启 bot
docker compose up -d --force-recreate  # 全量重建（含读取新的 .env）
```

### 改 Caddyfile（加新域名/反代）

```bash
vim ~/projects/caddy/Caddyfile
docker exec caddy caddy reload --config /etc/caddy/Caddyfile  # 热重载，不掉连接
```

### 改 .env（密钥变更）

```bash
cd ~/projects/chinese-semantle
vim .env
docker compose up -d --force-recreate  # 必须重建，不能 restart（restart 不重读 .env）
```

## 关键文件清单

| 文件 | 说明 |
|---|---|
| `~/projects/caddy/Caddyfile` | 反代规则、HTTPS 证书自动签发 |
| `~/projects/caddy/docker-compose.yml` | Caddy 容器编排 |
| `~/projects/chinese-semantle/docker-compose.yml` | backend + qqbot 编排 |
| `~/projects/chinese-semantle/.env` | 敏感凭据（**不进 Git**，权限 600） |
| `~/projects/chinese-semantle/qqbot/config.yaml` | bot 配置（`api_base: http://backend:8000`，**与本地不同**） |
| `~/projects/chinese-semantle/qqbot/nicknames.json` | 用户昵称持久化（容器重建不会丢） |
| `/srv/semantle-frontend/` | 前端静态文件，由 deploy.sh rsync 同步 |
| `/var/lib/docker/volumes/caddy_caddy_data/` | Let's Encrypt 证书存储（永远别删） |

## 故障排查

### 容器启动后立刻 OOM 重启

`docker logs <name> --tail 50` 看日志最后一行是什么：

- 卡在 `LightEngine: sqlite tables = ['puzzles']` 后没了 → 大概率内存不足
- 看 `docker inspect <name> --format '{{.State.OOMKilled}} {{.HostConfig.Memory}}'`

调高 `docker-compose.yml` 里的 `memory:`，然后 `docker compose up -d --force-recreate`。

### Caddy 签证书失败

```bash
docker logs caddy --tail 50 | grep -i 'error\|challenge'
```

常见原因：
- DNS A 记录未生效（用 `dig +short 你的域名 @114.114.114.114` 验证）
- Cloudflare 小黄云开了（**必须关掉，灰色 DNS only**）
- 80/443 端口未在腾讯云后台防火墙开放

### Bot 连不上后端

```bash
docker exec semantle-qqbot curl -sf http://backend:8000/api/health
```

如果失败：
- 检查 backend 容器是否 healthy: `docker compose ps`
- 检查两个容器是否在同一网络: `docker network inspect web`
- 检查 `qqbot/config.yaml` 里 `api_base` 是 `http://backend:8000`（不是 https://semantle.spacekid.me）

### 整机重启后服务没起来

容器都加了 `restart: always`，正常会自动起。如果没起：
```bash
sudo systemctl status docker     # 确认 docker daemon 起来了
docker ps -a                      # 看容器状态
docker compose -f ~/projects/caddy/docker-compose.yml up -d
docker compose -f ~/projects/chinese-semantle/docker-compose.yml up -d
```

## 安全基线（建议陆续做）

- [x] SSH 密钥登录（已开）
- [ ] 禁用密码登录：`sudo vim /etc/ssh/sshd_config` → `PasswordAuthentication no` → `sudo systemctl reload sshd`
- [ ] fail2ban 防 SSH 爆破：`sudo apt install -y fail2ban && sudo systemctl enable --now fail2ban`
- [ ] 定期 `docker image prune -f` 清理旧镜像
- [ ] 偶尔备份 `~/projects/chinese-semantle/qqbot/nicknames.json`（或长期改存 Postgres）

## 加新项目的标准动作

```bash
# 1. 新建项目目录
mkdir ~/projects/my-new-project && cd $_

# 2. 写 docker-compose.yml（关键：networks 加 web，不要映射 80/443）
cat > docker-compose.yml <<'EOF'
services:
  app:
    image: <some-image>
    container_name: my-new-app
    restart: always
    networks: [web]
    deploy:
      resources:
        limits:
          memory: 200M
networks:
  web:
    external: true
EOF

# 3. 在 Caddyfile 加 3 行
vim ~/projects/caddy/Caddyfile
# new.spacekid.me {
#     reverse_proxy my-new-app:3000
# }

# 4. 启动 + reload caddy
docker compose up -d
docker exec caddy caddy reload --config /etc/caddy/Caddyfile

# 5. Cloudflare 加 DNS A 记录指向 81.71.130.80（小黄云灰色）
```
