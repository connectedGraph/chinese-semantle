# 中文猜词 QQ 机器人（qqbot）

把 [chinese-semantle](../) 的猜词游戏接入 QQ 群 / 私聊。
**独立子项目，与 `backend/` 解耦**：通过 HTTP 调用现有线上后端（默认 `https://semantle.spacekid.me`），不影响项目根的 GitHub + Vercel 部署。

---

## 功能

| 场景 | 命令 | 说明 |
|---|---|---|
| 群聊 | `/新游戏` 或 `/随机计分游戏` | 开一局随机谜底（QQ bot 模式不计分） |
| 群聊 | `/每日挑战` | 开始今日的每日挑战 |
| 群聊 | `/指定游戏 编号或链接` | 例：`/指定游戏 QT5PWS` 或 `/指定游戏 https://semantle.spacekid.me/?game=QT5PWS` |
| 群聊 | `/提示` | 一局 5 次上限 |
| 群聊 | `/放弃` | 揭晓答案；提供「再来一局 / 复制分享链接」按钮 |
| 群聊 | 直接发汉字（1-8 字） | 即为猜词；非汉字静默忽略 |
| 私聊 | `/出题 苹果` | 自定义谜底；返回分享链接 + 按钮 |
| 通用 | `/改名 太空小孩` | 设置你在 bot 内的显示昵称（QQ v2 API 不下发真实昵称，需自助绑定）。<br>**注意**：群聊和私聊的 openid 不同，需要在两边各自 `/改名` 一次 |

每局猜词回复以 markdown 渲染历史 Top-10（命中谜底时全量），按 `🔴/🟠/⚪️ × 10` 渲染相似度色块。

---

## 运行方式

### 1. 本地开发（最简）

```bash
# 安装依赖（建议虚拟环境，与项目根 requirements.txt 隔离）
cd <repo-root>
python -m venv .venv-qqbot && source .venv-qqbot/bin/activate
pip install -r qqbot/requirements.txt

# 准备配置：复制示例并填入凭据
cp qqbot/config.example.yaml qqbot/config.yaml
# 编辑 qqbot/config.yaml，填 appid 与 secret

# 启动（必须从仓库根目录运行，使用 -m 模式以让 qqbot 包能被 import）
python -m qqbot.bot
```

> macOS 用户若遇 `SSLCertVerificationError`，先注入 certifi 提供的 CA bundle：
>
> ```bash
> pip install --upgrade certifi
> export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
> python -m qqbot.bot
> ```
>
> `qqbot/config.yaml` 已加入 `.gitignore`，不会被 commit。建议把权限设为 600：
>
> ```bash
> chmod 600 qqbot/config.yaml
> ```

### 2. 通过环境变量注入凭据（部署推荐）

不必把 secret 落盘，直接：

```bash
export QQBOT_APPID="1903828349"
export QQBOT_APPSECRET="xxxxxxx"
export QQBOT_API_BASE="https://semantle.spacekid.me"
python -m qqbot.bot
```

环境变量优先级高于 `config.yaml`。

### 3. Docker（部署预留）

```bash
# 在仓库根构建（Dockerfile 路径在 qqbot/Dockerfile）
docker build -t chinese-semantle-qqbot -f qqbot/Dockerfile .

# 推荐：env 注入凭据 + 重启策略保活
docker run -d --name semantle-qqbot --restart=always \
  -e QQBOT_APPID=xxx -e QQBOT_APPSECRET=xxx \
  chinese-semantle-qqbot

# 或挂载 config.yaml（注意：此模式 secret 落盘）
docker run -d --name semantle-qqbot --restart=always \
  -v $(pwd)/qqbot/config.yaml:/app/qqbot/config.yaml:ro \
  chinese-semantle-qqbot
```

### 4. systemd（裸机服务器部署预留）

```ini
# /etc/systemd/system/semantle-qqbot.service
[Unit]
Description=Chinese Semantle QQ Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chinese-semantle
Environment="QQBOT_APPID=xxx"
Environment="QQBOT_APPSECRET=xxx"
Environment="QQBOT_API_BASE=https://semantle.spacekid.me"
ExecStart=/opt/chinese-semantle/.venv/bin/python -m qqbot.bot
Restart=always
RestartSec=5
User=botuser

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now semantle-qqbot
sudo journalctl -u semantle-qqbot -f
```

---

## 配置项

| 字段 | 默认 | 环境变量 | 说明 |
|---|---|---|---|
| `appid` | — | `QQBOT_APPID` | QQ 开放平台 AppID |
| `secret` | — | `QQBOT_APPSECRET` | QQ 开放平台 AppSecret |
| `sandbox` | `false` | `QQBOT_SANDBOX` | 沙箱环境开关 |
| `api_base` | `https://semantle.spacekid.me` | `QQBOT_API_BASE` | 后端 API 基址 |
| `share_url_prefix` | `https://semantle.spacekid.me/?game=` | — | 分享链接前缀 |
| `history_top_n` | `10` | — | 默认历史展示条数 |
| `session_history_cap` | `500` | — | 单局缓存上限（兜底） |
| `api_timeout` | `15` | — | 后端请求超时（秒） |
| `log_level` | `INFO` | — | 日志等级 |

---

## 架构与设计要点

### 与现有 Vercel 部署的隔离

- **物理隔离**：`qqbot/` 不在 `vercel.json` 的 builds/routes 中，新增不会被 Vercel build 拉入 lambda（不受 250MB 限制影响）
- **依赖隔离**：`qqbot/requirements.txt` 与项目根 `requirements.txt` 完全独立
- **构建隔离**：bot 不消费 `backend/data/precomputed/`，不参与现有 build_precomputed 流水线
- **可移性**：将来想拆出独立仓库 → 整目录搬走即可（零跨目录引用）

### 进程模型

- botpy WebSocket 客户端模式，**不需要公网入口**（出向 HTTPS 即可）
- 长连接进程，**不能放在 Vercel/serverless** 上；推荐 Docker/systemd/Cloud Studio 长驻容器
- 全程使用**被动回复**（每条用户消息携带 `msg_id`），规避主动消息每月每群 4 条上限

### 会话状态

- 一个群 = 一局活跃游戏；用 `group_openid` 作为 conversation_id
- 私聊也独立成局（`user_openid` 作为 conversation_id）
- 内存缓存 puzzle_code + history + 标志位，进程重启即丢
- **后端冷启动失活时（HTTP 404）**：bot 自动用 puzzle_code 重建 game 并按 history 顺序重放，玩家无感

### 同 puzzle 内同词去重

后端不会对重复词去重（同词猜两次会写两条 record + 消耗 guess_count），bot 侧做了一层缓存：同一谜底已猜过的词不再调用后端，直接复用上次结果。

---

## 已知限制

1. **进程重启丢局**：当前会话仅在内存中。重启后玩家可通过 `/指定游戏 #编号` 找回（编号在每条 bot 回复里都有展示）。
2. **不参与排行榜**：QQ bot 模式一律不计分，避免群组多人协作扰乱排行榜。
3. **被动消息时效**：每条用户消息有 5 分钟 / 5 次回复上限，超时仅能用主动消息（每月每群 4 条），bot 全程被动回复以规避此限制。
4. **群聊指令按钮 `enter=true` 不生效**：群里只能填充输入框，需用户手动发出（QQ 平台限制，仅单聊 enter 可用）。

---

## 故障排查

| 现象 | 可能原因与解决 |
|---|---|
| 启动报 `缺少 appid / secret` | 检查 `qqbot/config.yaml` 或环境变量 `QQBOT_APPID/SECRET` |
| 启动报 `已有 bot 进程在跑（PID=...）` | **多实例守护已生效**，按提示 `pkill -9 -f 'qqbot.bot'` 后重启 |
| `on_ready` 后无任何反应 | 检查 QQ 开放平台「事件订阅」是否已开启**群消息**和**单聊消息** |
| 机器人回复内容错乱（puzzle_code 跳变、相似度突变） | **极可能是多实例同时跑**：`pgrep -f qqbot.bot` 检查；现已加单实例锁，再现可能性极低 |
| 日志大量 `[DEDUP] 消息被去重 (40054005)` | 同上，多实例竞争 `(msg_id, msg_seq=1)`；杀掉所有进程重启即可 |
| markdown 消息发不出 / 回退到纯文本 | 检查机器人是否已被审核通过；2026/04/23 之前需 markdown 模板报备 |
| 后端 health 自检失败 | curl `https://semantle.spacekid.me/api/health` 验证后端可达性 |
| `SSLCertVerificationError`（连 `bots.qq.com:443` 失败） | macOS Python 默认证书链不全。`pip install -U certifi && export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"` |

### 多实例陷阱（重要）

> ⚠️ **严禁同时跑多个 bot 进程**！同一 AppID 多进程同时连 QQ 网关会导致：
> 1. 每个进程都收到同一条用户消息事件
> 2. 互相竞争 `(msg_id, msg_seq=1)` 触发服务端 40054005 去重
> 3. 用户客户端看到的回复来自不同进程，puzzle_code / 相似度状态错乱
>
> 现已加 **fcntl 文件锁** (`qqbot/.bot.lock`)，新进程会被立即拒绝；
> 启动前如担心残留进程，先：
> ```bash
> pkill -9 -f 'qqbot.bot'
> ```

---

## 测试本地连通性（不启动 bot）

```bash
# 验证后端可用
curl https://semantle.spacekid.me/api/health
# 应返回 {"ok": true, "vocab_size": 128898}

# 验证 Python 导入正常（不会真正连 QQ 网关）
python -c "from qqbot import config, api_client, handlers, renderer, keyboard, session, bot; print('OK')"
```

---

## License

继承项目根 LICENSE。
