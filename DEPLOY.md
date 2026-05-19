# 部署指南

> ⚠️ **当前生产环境是 VPS + Docker，不是 Vercel**。
> 请优先阅读 [DEPLOY-VPS.md](./DEPLOY-VPS.md)。
> 本文档是开发阶段尝试过的 Vercel + Neon serverless 方案，作为备选保留归档。

## TL;DR — 日常更新一句话

**本机 `git push` 后，SSH 上服务器跑 `./deploy.sh` 一键更新。**

展开就是这两步：

```bash
# 1. 本机：把改动推到 GitHub
git push

# 2. 服务器：拉新代码 → 同步前端 → 重建镜像 → 滚动重启 → 健康检查
ssh ubuntu@81.71.130.80
cd ~/projects/chinese-semantle && ./deploy.sh
```

`deploy.sh` 内部做了 5 件事：`git pull` → `rsync` 前端到 `/srv/semantle-frontend/` → `docker compose build` → `docker compose up -d` → `curl /api/health`。完整运维细节、故障排查、Caddy 配置见 [DEPLOY-VPS.md](./DEPLOY-VPS.md)。

---

# 历史归档：部署到 Vercel + Neon

> 以下内容为开发阶段尝试 Vercel serverless 部署时整理的笔记，仅作备选方案保留。生产环境**不再走这条路径**。

本文档记录把猜词游戏部署到 Vercel（计算）+ Neon（Postgres 数据库）的完整步骤。

## 前置准备

1. **GitHub 账号**：仓库需推送到 GitHub
2. **Vercel 账号**：[vercel.com](https://vercel.com)
3. **Neon 账号**：[neon.tech](https://neon.tech)
4. **本地已生成 precomputed 数据**：

   ```bash
   cd backend
   # ⚠️ 重要：构建期使用的 secret 必须与 Vercel 上的 PUZZLE_SECRET 完全一致
   # 推荐先生成 secret，再用同一个 secret 跑构建
   PUZZLE_SECRET="你生成的 secret" python -m scripts.build_precomputed --rebuild
   ```

   产物（**必须 commit 到仓库**）：
   - `backend/data/precomputed/neighbors.sqlite`（约 78MB，Top-3000 邻居）
   - `backend/data/precomputed/puzzle_codes.json`（约 32KB）

   **细节见**「[常见问题：开局正常但所有词都显示"远"](#开局正常但所有词都显示远) 」。

## Step 1：把仓库推到 GitHub

```bash
cd 20260506144237
git init
git add .
git commit -m "Initial commit: chinese semantle"
gh repo create chinese-semantle --public --source=. --push
# 或在 GitHub 网站建仓库后：
# git remote add origin git@github.com:你的用户名/chinese-semantle.git
# git push -u origin main
```

⚠️ **检查**：

```bash
# 1. 确认 sqlite 进了 git
git ls-files backend/data/precomputed/
# 应该输出：
#   backend/data/precomputed/neighbors.sqlite
#   backend/data/precomputed/puzzle_codes.json

# 2. 确认 sqlite 体积 ≤ 100MB（GitHub 单文件硬限制）
ls -lh backend/data/precomputed/neighbors.sqlite

# 3. 确认词向量大文件没进 git
git ls-files backend/data/ | grep -E '\.(kv|npy|bin|tar\.gz)$'
# 应该没有输出
```

## Step 2：在 Neon 创建数据库

1. 登录 [Neon Console](https://console.neon.tech)
2. **Create Project** → 选择 region（推荐 Singapore / Hong Kong / Tokyo，国内访问较快）
3. 创建后 → **Dashboard** → **Connection Details** → 复制 **Pooled connection** URL（含 `-pooler`）
4. URL 形如：
   ```
   postgresql://user:password@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```

> 💡 不需要手动建表。后端首次启动会自动 `CREATE TABLE IF NOT EXISTS leaderboard ...`。

> **推荐**：直接在 Vercel 的 **Storage → Add Integration → Neon** 创建，会自动注入 `DATABASE_URL` 等环境变量到项目。

## Step 3：在 Vercel 导入项目

1. 登录 Vercel → **Add New** → **Project**
2. 选择上一步推送的 GitHub 仓库
3. **Framework Preset**：选 **Other**（不要选 Services / Next.js / FastAPI 等任何具体框架，让 Vercel 完全按 `vercel.json` 部署）
4. **Root Directory**：保持 `./`
5. **Build / Output Settings**：全部留空（`vercel.json` 已规定）
6. **Environment Variables**（应用到 Production / Preview / Development 三个环境）：

   | Name | Value | 备注 |
   |---|---|---|
   | `PUZZLE_SECRET` | 32+ 位随机字符串 | **必填**。用 `openssl rand -base64 32` 生成。**必须与 build_precomputed 时用的 secret 一致**！ |
   | `DATABASE_URL` | Neon Pooled connection URL | **必填**。如果用 Neon 集成会自动注入 |
   | `SEMANTLE_ENGINE` | `light` | 可选；`api/index.py` 已默认 light |

7. **Deploy**，等待 1-3 分钟

## Step 4：验证

```bash
DOMAIN=https://你的域名.vercel.app

# 1. 健康检查（不依赖数据库）
curl -s $DOMAIN/api/health
# 期望: {"ok":true,"vocab_size":数万}

# 2. 今日谜题（不依赖数据库）
curl -s $DOMAIN/api/daily/today
# 期望: {"date":"...","puzzle_code":"......","target_length":2}

# 3. 创建带 hint 的随机游戏
curl -s -X POST $DOMAIN/api/games -H "Content-Type: application/json" -d '{"mode":"random","hint_count":3}' | python3 -m json.tool
# 期望: hints 数组里有 3 个真实词（不是空数组）

# 4. 排行榜接口（依赖数据库）
curl -s $DOMAIN/api/leaderboard/某个有效code
# 期望: 200 + JSON。500 → DATABASE_URL 没注入或不可达
```

第 3 步 hints 是空数组 → 看 [PUZZLE_SECRET 不一致](#开局正常但所有词都显示远) 章节。

完整流程自检：玩一局 → 猜中 → 提交昵称 → 看到排行榜更新 → 复制分享链接 → 用无痕窗口打开 → 应进入同一谜底。

---

## 常见问题

### `vercel.json` 必备配置（不要轻易改）

```json
{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python",
      "config": {
        "maxLambdaSize": "250mb",
        "runtime": "python3.11",
        "includeFiles": "backend/**"
      }
    },
    { "src": "frontend/**", "use": "@vercel/static" }
  ],
  "routes": [...]
}
```

**关键点**：

- `includeFiles: "backend/**"` 必须有，否则 `from app.main import app` 会 `ModuleNotFoundError`。
  不要写 `backend/{a/**,b/**}` 的 brace 形式，`@vercel/python` 不展开。
- `maxLambdaSize: "250mb"` 对应 Vercel Hobby 上限。

### Function 超过 250MB 包大小

最常见两个原因：

1. **`backend/requirements.txt` 被自动检测**：只要这个文件存在，`@vercel/python` 会按它装依赖。
   本项目的 `backend/requirements.txt` 含 `gensim + numpy`（200MB+），会瞬间爆。
   → 本仓库已重命名为 `backend/requirements-local.txt`。如果你 fork 时合并出冲突，确保**根目录 `requirements.txt`** 是 lambda 用的精简版（仅 fastapi/asyncpg/pydantic/pyyaml），`backend/` 下不要有名为 `requirements.txt` 的文件。

2. **`backend/data/precomputed/neighbors.sqlite` 太大**：1781 谜底 × 3000 邻居的 sqlite 通过 blob 存储约 78MB。如果你看到 100MB+，多半是早期版本残留的 `neighbor_index` 表 —— 重跑 `build_precomputed.py --rebuild` 会自动清掉。

### Cold start 慢（>5 秒）

LightEngine 启动会一次性解压所有 1781 个邻居 blob 到内存（Top-3000 时 ~240MB），实测约 3-4 秒。如果远超：

- Vercel function logs 应看到 `Engine mode: light` + `LightEngine ready: 1781 puzzles, ... known words, ... common words`
- **不应**看到 `Loading word vector engine` / `KeyedVectors` —— 那是 LocalEngine 误启
- 检查 `vercel.json` 没把 `embedding.kv` / `*.npy` / `*.bin` 拉进 lambda（应被 `.gitignore` 排除）

### 开局正常但所有词都显示"远"

最容易掉坑的一个 ⚠️。**症状**：

- `/api/health` 正常返回 vocab_size
- 创建游戏的 `hints` 是空数组 `[]`
- 猜任何词 similarity 都是 -1.0、proximity_rank 都是 null
- 提示功能报「当前游戏没有可用的提示候选」

**根因**：构建 `neighbors.sqlite` 时用的 `PUZZLE_SECRET` ≠ Vercel 上的 `PUZZLE_SECRET`。

`puzzle_code` 由 `HMAC(PUZZLE_SECRET, target_word)` 派生，两边 secret 不同会导致：
- 构建期写进 sqlite 的 code（如 `PTN2JZ`）和运行期 `encode_word()` 算出的 code（如 `KNTFZH`）对不上
- `top_k(target)` 永远查不到 → `top_neighbors == []`，rank_map 为空
- `similarity()` 也永远 miss → 永远返 -1.0

**修复**：本地用生产 secret 重新构建一次。

```bash
cd backend
PUZZLE_SECRET="你 Vercel 上的同一个 secret" python -m scripts.build_precomputed --rebuild
git add backend/data/precomputed/
git commit -m "rebuild precomputed with production secret"
git push
```

⚠️ **不要省略 `--rebuild`**：脚本是增量的，只会跳过"已有 code"，但旧 code 是用旧 secret 算的，新 secret 算的所有 code 都"不存在"，结果会重新写一遍但旧 row 还在 → DB 体积翻倍。

### 排行榜提交报 "submit_token 无效或已过期"

- token 1 小时有效，玩完赶紧提交
- 检查前端是否携带了 `submit_token` 字段
- 如果改过 `PUZZLE_SECRET`：旧 token 全部失效，正常现象

### Neon 报 "too many connections"

Neon Free Tier 有连接数上限。本项目已用 connection pool（max_size=4）；如果仍超：
- 把 Vercel function 的 region 设成与 Neon 同区（Vercel Settings → Functions → Region）
- 务必使用 Neon 的 **Pooled connection**（URL 中含 `-pooler`）

### 修改 PUZZLE_SECRET 的影响（上线后慎改）

- 所有 puzzle_code 会全部变更（同一个谜底词派生新编号）
- 旧分享链接全部失效
- 旧排行榜的 puzzle_code 与新编号不匹配 → 历史数据"丢失"
- 改了 secret 后**必须**用新 secret 重跑 `build_precomputed --rebuild` 并 push
- **建议**：上线前定好 secret，之后不要改

### Vercel 显示 Services / 多服务模式提示

某些新版 Vercel UI 会因为仓库里有 `backend/` + `frontend/` 而推送「Services」预设。**不要选**，会忽略 `vercel.json`。在 Application Preset 下拉里选 **Other** / **No Framework**。

---

## 部署后常用命令

### 扩充白名单

```bash
# 1. 编辑 backend/data/whitelist.txt
# 2. 重新构建词表
cd backend
python -m scripts.build_wordlist
# 3. 计算新增谜底的邻居（增量；新 secret 时务必加 --rebuild）
PUZZLE_SECRET="生产 secret" python -m scripts.build_precomputed
# 4. push 触发 Vercel 自动部署
git add backend/data/ && git commit -m "Add new puzzles" && git push
```

### Vercel 区域 ↔ Neon 区域对齐

延迟敏感的话，把两者放在同一 region：
- Vercel 项目 Settings → Functions → Region → 选与 Neon 一致
- 例：Neon 在 `ap-southeast-1` → Vercel 选 Singapore (`sin1`)

### 自定义域名

Vercel 项目 Settings → Domains → 添加自定义域名（自动签 HTTPS 证书）。

---

## 一些不容易看出来的「为什么这么写」

`api/index.py` 里 `app` 必须**在模块顶层无条件赋值**。`@vercel/python` 用静态分析找 `app`，不会执行代码：

```python
# ✅ 顶层无条件赋值
app: FastAPI = FastAPI(...)
try:
    from app.main import app as _real
    app = _real
except Exception:
    ...

# ❌ 条件赋值，部署期会报 Could not find a top-level "app"
try:
    from app.main import app
except Exception:
    app = FastAPI(...)
```

`engine_light.py` 里打开 sqlite 必须用 `?mode=ro&immutable=1`。Vercel lambda `/var/task` 是只读 fs，sqlite 即使 `mode=ro` 仍会尝试创建 `-journal` / `-wal` 文件，触发 `EROFS` 然后报一个让人迷惑的 `unable to open database file`：

```python
sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, ...)
```

`immutable=1` 告诉 SQLite「文件绝对不会变」→ 跳过所有 journal/lock 机制 → 在只读 fs 上工作正常。
