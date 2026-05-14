# 部署到 Vercel + Neon

本文档记录把猜词游戏部署到 Vercel（计算）+ Neon（Postgres 数据库）的完整步骤。

## 前置准备

1. **GitHub 账号**：仓库需推送到 GitHub
2. **Vercel 账号**：[vercel.com](https://vercel.com)
3. **Neon 账号**：[neon.tech](https://neon.tech)
4. **本地已生成 precomputed 数据**：
   ```bash
   cd backend
   python -m scripts.build_precomputed
   ```
   产物位于 `backend/data/precomputed/`，**必须 commit 到仓库**。

## Step 1：把仓库推到 GitHub

```bash
cd 20260506144237
git init
git add .
git commit -m "Initial commit: chinese semantle v0.2"
gh repo create chinese-semantle --public --source=. --push
# 或在 GitHub 网站上手动建仓库后：
# git remote add origin git@github.com:你的用户名/chinese-semantle.git
# git push -u origin main
```

⚠️ **检查**：commit 前确认 `backend/data/precomputed/neighbors.sqlite` 已被 git 跟踪（不在 .gitignore 里）。

```bash
git ls-files backend/data/precomputed/
# 应该输出：
#   backend/data/precomputed/neighbors.sqlite
#   backend/data/precomputed/puzzle_codes.json
```

⚠️ **不要提交** 词向量大文件：`embedding.kv`、`*.npy`、`*.bin`（已在 .gitignore 中）。

## Step 2：在 Neon 创建数据库

1. 登录 [Neon Console](https://console.neon.tech)
2. **Create Project** → 选择 region（推荐 Singapore / Hong Kong / Tokyo，国内访问较快）
3. 创建后 → **Dashboard** → **Connection Details** → 复制 **Pooled connection** URL
4. URL 形如：
   ```
   postgresql://user:password@ep-xxxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
   保存好，下一步要用。

> 💡 不需要手动建表。后端首次启动会自动 `CREATE TABLE IF NOT EXISTS leaderboard ...`。

## Step 3：在 Vercel 导入项目

1. 登录 Vercel → **Add New** → **Project**
2. 选择上一步推送的 GitHub 仓库
3. **Framework Preset**：选 **Other**（不要选 Next.js / Vite，让 Vercel 走 vercel.json）
4. **Root Directory**：保持 `./`
5. **Build / Output Settings**：保持空白（vercel.json 已经规定了）
6. **Environment Variables**（重要）：

   | Name | Value | 备注 |
   |---|---|---|
   | `PUZZLE_SECRET` | 一个 32+ 位的随机字符串 | **必填**。用 `openssl rand -base64 32` 生成 |
   | `DATABASE_URL` | 上一步 Neon 的连接串 | **必填** |
   | `SEMANTLE_ENGINE` | `light` | 可选；vercel.json 已默认 light |

7. 点击 **Deploy**，等待 1-3 分钟

## Step 4：验证

部署成功后，访问 Vercel 给的 URL（如 `https://chinese-semantle.vercel.app`）：

1. **健康检查**：`https://你的域名/api/health` → 应返回
   ```json
   {"ok": true, "vocab_size": 800000+}
   ```

2. **首页**：根 URL 应直接打开游戏

3. **API 文档**：`/docs`（OpenAPI Swagger UI）

4. **完整流程**：玩一局猜中 → 提交昵称 → 看到排行榜更新 → 复制分享链接 → 用无痕窗口开链接 → 应进入同一谜底

## 常见问题

### Function 超过 250MB 包大小

精简 `requirements.txt`，**绝对不要** 加 `gensim` / `numpy<2.0` / `scipy`。
LightEngine 只用 Python 标准库（sqlite3、gzip、json、hmac）+ FastAPI + asyncpg。

### Cold start 慢（>5 秒）

检查是否被强制走了 LocalEngine。
- 在 Vercel function logs 中应看到 `Engine mode: light` + `LightEngine ready`
- 不应出现 `Loading word vector engine` / `KeyedVectors`

### 排行榜提交报 "submit_token 无效或已过期"

- token 1 小时有效，玩完赶紧提交
- 检查前端是否携带了 `submit_token` 字段
- 如果改过 `PUZZLE_SECRET`：旧 token 全部失效，正常现象

### Neon 报 "too many connections"

Neon Free Tier 有连接数上限。已经用了 connection pool（max_size=4）；
如果仍超，把 Vercel 的 function 改为 region=Neon 同区，并改用 Neon 的 **Pooled connection**（URL 中含 `-pooler`）。

### 想要把 Vercel 域名换掉

- Vercel 项目 Settings → Domains → 添加自定义域名

### 修改 PUZZLE_SECRET 的影响

- 所有 puzzle_code 会全部变更（同一个谜底词派生新编号）
- 旧分享链接全部失效
- 旧排行榜的 puzzle_code 与新编号不一致 → 等于历史数据"丢失"
- **建议**：上线前定好 SECRET，之后不要改

## 后续：自定义运营

### 扩充白名单

```bash
# 1. 编辑 backend/data/whitelist.txt
# 2. 重新构建词表
cd backend
python -m scripts.build_wordlist
# 3. 增量计算新增谜底的邻居（已有的不会重跑）
python -m scripts.build_precomputed
# 4. commit + push，Vercel 会自动重新部署
git add backend/data/
git commit -m "Add new puzzles"
git push
```

### 切换回 Vercel 区 + Neon 区

延迟敏感的话，把两者放在同一 region：
- Vercel 项目 Settings → Functions → Region → 选与 Neon 一致的 region
- 例：Neon 在 `ap-southeast-1` → Vercel 选 Singapore (`sin1`)
