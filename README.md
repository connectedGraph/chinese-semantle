# 猜词 · Chinese Semantle

一个中文版的 [Semantle](https://semantle.com/) 复刻 —— 通过语义相似度玩猜词游戏。

🎮 **在线试玩**：<https://chinese-semantle.vercel.app/>

- 🌐 **Web 小游戏**：清爽高级、颜色克制的界面
- 🔗 **可分享的谜底编号**：每个谜底有稳定的 6 位编号（HMAC 派生，不可反推），URL `?game=XXXXXX` 一键转发同一局
- 📅 **每日挑战 + 回溯**：每天一题，可回溯任意已发布日期
- 🏆 **排行榜**：每个谜底单独排行；只接受随机模式且未使用提示的成绩
- 🤖 **完整 API**：FastAPI + OpenAPI 文档，预留给 QQ 机器人 / Agent Skill
- ☁️ **一键部署**：Vercel + Neon Postgres；Lambda 包 ~200MB，冷启动 ~3-4 秒

## 项目结构

```
.
├── api/
│   └── index.py              # Vercel function 入口（顶层 export FastAPI app）
├── backend/                  # 业务代码（本地开发也用同一份）
│   ├── app/
│   │   ├── main.py           # FastAPI 路由
│   │   ├── engine.py         # LocalEngine（gensim + 词向量，~116MB）
│   │   ├── engine_light.py   # LightEngine（读预计算邻居 SQLite，~240MB 内存）
│   │   ├── engine_base.py    # 引擎抽象与运行时选择器
│   │   ├── game.py           # 游戏会话
│   │   ├── daily.py          # 每日挑战 / 日历
│   │   ├── puzzle_codes.py   # HMAC 编号 + code↔word 反向表
│   │   ├── leaderboard/
│   │   │   ├── repo.py       # Memory / Postgres 双后端
│   │   │   └── tokens.py     # submit_token HMAC 签名
│   │   └── models.py         # Pydantic 模型
│   ├── data/
│   │   ├── whitelist.txt     # 谜底白名单源（人工维护）
│   │   ├── target_words.txt  # 构建产物（本地 LocalEngine 加载）
│   │   ├── daily_pool.txt    # 每日挑战候选池
│   │   ├── daily_challenges.yaml  # 每日挑战手工配置
│   │   └── precomputed/      # ⭐ Vercel 部署依赖的预计算数据
│   │       ├── neighbors.sqlite   # 所有谜底的 Top-3000 邻居（~78MB）
│   │       └── puzzle_codes.json  # code → word 反向表（~32KB）
│   ├── scripts/
│   │   ├── build_wordlist.py     # whitelist.txt → target_words.txt
│   │   └── build_precomputed.py  # ⭐ 生成 precomputed/ 目录
│   └── requirements-local.txt    # 本地开发完整依赖（含 gensim）
├── frontend/                     # 静态前端（原生 HTML/CSS/JS，无构建）
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── requirements.txt              # ⭐ Vercel runtime 精简依赖（无 gensim）
├── vercel.json                   # 路由 + includeFiles 配置
├── .env.example                  # 环境变量样例
├── DEPLOY.md                     # 部署完整步骤 + 踩坑速查
└── README.md
```

## 本地开发

### 1. 准备词向量

下载腾讯 AI Lab 中文词向量（轻量版即可），放到 `backend/data/`：

```
backend/data/light_Tencent_AILab_ChineseEmbedding.bin   # 约 116MB
```

首次启动时会自动缓存为 `embedding.kv`（之后秒级加载）。

### 2. 启动后端

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-local.txt
bash run.sh                # 监听 0.0.0.0:8000
```

默认走 `auto` 引擎模式：有 `precomputed/neighbors.sqlite` 就用 LightEngine，否则用 LocalEngine。如果想强制 LocalEngine（实时计算）：

```bash
SEMANTLE_ENGINE=local bash run.sh
```

### 3. 启动前端

```bash
cd frontend
python3 -m http.server 5173
# 访问 http://localhost:5173
```

前端默认连接 `http://localhost:8000`。

### 4. 生成预计算数据（首次必做，部署前必做）

LocalEngine 跑通后，为 Vercel 部署做准备：

```bash
cd backend
source .venv/bin/activate

# ⚠️ 重要：构建期 PUZZLE_SECRET 必须与生产环境 (Vercel) 完全一致
# 推荐先生成生产 secret，然后用同一个 secret 跑构建
export PUZZLE_SECRET="$(openssl rand -base64 32)"
python -m scripts.build_precomputed --rebuild
```

耗时约 60 秒（取决于机器），产物：

- `backend/data/precomputed/neighbors.sqlite`（约 78MB，Top-3000 邻居）
- `backend/data/precomputed/puzzle_codes.json`（约 32KB）

这两个文件**必须 commit 到仓库**（已在 `.gitignore` 中放行），Vercel 上 LightEngine 会读取它们。

> **如果改过 secret 或词表**：必须加 `--rebuild`。否则增量构建会让旧 row 残留在 DB 里，体积翻倍。详见 [DEPLOY.md 常见问题](./DEPLOY.md#开局正常但所有词都显示远)。

### 5. 扩充白名单 → 重跑构建

```bash
# 1. 编辑 backend/data/whitelist.txt
python -m scripts.build_wordlist           # 生成新的 target_words.txt
PUZZLE_SECRET="生产 secret" python -m scripts.build_precomputed
# 词表新增时不需要 --rebuild（增量构建）；只在 secret 变更时才需要 --rebuild
```

## 部署到 Vercel + Neon

详见 [DEPLOY.md](./DEPLOY.md)。简要步骤：

1. 生成 `PUZZLE_SECRET`：`openssl rand -base64 32`，记下来
2. 用同一个 secret 跑 `build_precomputed --rebuild`，commit 产物
3. 把仓库（含 `backend/data/precomputed/`）推送到 GitHub
4. 在 [Neon](https://neon.tech) 创建 Postgres 项目（推荐 Singapore region）
5. 在 Vercel 导入仓库，**Framework Preset 选 Other**，设环境变量：
   - `PUZZLE_SECRET`：第 1 步的同一个 secret
   - `DATABASE_URL`：Neon 的 Pooled connection
6. 部署 → `/api/health` 应返回 `{"ok": true, "vocab_size": ...}`

## API 速览

完整 OpenAPI 文档：访问 `/docs`（页面 footer 也有链接）。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/games` | 新建一局；可选 `puzzle_code` 指定谜底，或 `mode=daily + daily_date` 进每日挑战 |
| `GET`  | `/api/games/by-code/{code}` | 便捷接口：按编号直接开局 |
| `GET`  | `/api/games/{game_id}` | 获取游戏状态 |
| `POST` | `/api/games/{game_id}/guess` | 提交猜词；命中且计分则下发 `submit_token` |
| `POST` | `/api/games/{game_id}/hint` | 自动提示（一局上限 5 次，使用后失去排行榜资格） |
| `POST` | `/api/games/{game_id}/giveup` | 放弃，揭晓答案 |
| `GET`  | `/api/puzzles/{code}/peek` | 查询某编号是否存在 + 谜底字数（不泄露谜底） |
| `GET`  | `/api/daily/today` | 获取今日每日挑战的元信息 |
| `GET`  | `/api/daily/calendar` | 获取一段日期的每日挑战日历 |
| `GET`  | `/api/leaderboard/{code}` | 拿排行榜 Top-N（默认 3） |
| `POST` | `/api/leaderboard/{code}` | 提交成绩；需 `submit_token` |
| `GET`  | `/api/health` | 健康检查 |

## 关键设计

### 谜底编号（puzzle_code）

```
code = base32(HMAC-SHA256(PUZZLE_SECRET, target_word)[:4])[:6]
```

- **不可反推**：拿到编号反推谜底需要爆破 SECRET 或穷举词典
- **稳定**：同一个词永远对应同一个编号；白名单增删不影响已有编号
- **反向查询**：构建期生成 `puzzle_codes.json` 反向表，运行时 O(1) `code → word`
- ⚠️ **构建期 secret 必须与运行时一致**，否则 sqlite 里的 code 全部对不上 → 整个游戏失灵（详见 [DEPLOY.md](./DEPLOY.md#开局正常但所有词都显示远)）

### 双引擎架构

| 引擎 | 数据 | 内存 | 启动 | 适用场景 |
|---|---|---|---|---|
| LocalEngine | 腾讯词向量 ~116MB | ~500MB | ~3s | 本地开发；扩词后跑 build_precomputed |
| LightEngine | precomputed/neighbors.sqlite | ~240MB | ~4s | Vercel 部署 |

两者实现同一个 `EngineProtocol`，业务代码（`game.py`）完全不感知差异。

LightEngine 的工作机制：构建期把每个谜底的 Top-3000 邻居 gzip 后存进 sqlite blob，启动时全部解压到 per-puzzle 内存 dict，之后所有 `top_k` / `similarity` 查询都是内存 O(1)。同时启用 `is_common` 高频池过滤（读 `target_words.txt`），保证提示词偏好「果树」而不是「畦」。

### 排行榜防伪

```
guess（成功）→ 服务端发 submit_token = HMAC(SECRET, {code, count, game_id, exp})
              ↓
submit_score → 客户端带 token → 服务端验签 + 校验未过期 + code 匹配
```

- 使用过提示的玩家：服务端不下发 token → 无法提交
- 篡改 guess_count：签名校验失败
- token 1 小时有效

## 算法说明

- **相似度**：余弦相似度（LocalEngine 实时计算 / LightEngine 查预计算表）
- **接近度（Proximity）**：每个谜底的 Top-3000 邻居（按相似度降序）
  - 排名 ∈ [1, 300]：🔥 非常接近（hot）
  - 排名 ∈ [301, 1000]：🌡️ 接近（warm）
  - 排名 ∈ [1001, 3000]：远但有 sim 数值参考
  - 不在 Top-3000：❄️ 低相似度（cold） —— LightEngine 模式下显示为「低相似度」字样而非具体数字

## 路线

- [x] 可分享谜底编号 + URL
- [x] 排行榜（Neon + Memory 双后端）
- [x] 完整 API + OpenAPI 文档
- [x] Vercel 部署
- [x] 每日挑战 + 历史回溯
- [ ] QQ 机器人接入（player_name 字段已预留）
- [ ] 词表运营化界面
