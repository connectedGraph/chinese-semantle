# Agent 对战模式（你 vs DeepSeek）

在原版中文 Semantle 基础上新增的二次开发：让 **DeepSeek Agent** 和你在**同一个隐藏答案**上比赛谁先猜中。

## 玩法

1. 打开 `frontend/race.html`（本地开发 http://127.0.0.1:5174/race.html）
2. 页面自动开一局：人类和 Agent 各拿一局**相同谜底**的游戏
3. 你在左边输入中文词猜；Agent 在右边通过工具调用自动猜
4. **比谁猜中用的猜测次数更少**（LLM 回复快是速度优势，不参与比较）
5. Agent 猜中后会停下，你继续猜直到猜中或点「放弃」，然后结算：
   - 双方都猜中 → 次数少者胜
   - 仅一方猜中 → 该方胜
   - 都没猜中 → 平局

**遮蔽机制**：对局中 Agent 的思维链、工具调用和猜测结果默认被遮住（避免偷看），点右上角「看 Agent 思考」可随时展开；对局结束时自动全部揭晓。

Agent 不能偷看答案，只能靠 `guess` 试探 + `view_topk` 整理自己已猜过的结果，因此和人类一样靠推理收敛。

两边结果都按 **一行一词一相似度一rank** 展示。

## Agent 的两个工具

Agent **只有这两个工具**，且都支持并行：

| 工具 | 参数 | 说明 |
|---|---|---|
| `guess` | `words: string[]` | 批量猜词，返回每个词的相似度百分比与排名（rank 越小越近） |
| `view_topk` | `k: int = -1` | 把**当前已经猜过的词**按接近度整理好返回（相似度降序 / rank 升序，一行一词一相似度一rank）；`k=-1` 返回全部已猜词。不产生新猜测、不泄露答案 |

并行体现在两处：
- 模型可在**同一回合**发出多个 `tool_call`，后端用 `asyncio.gather` **并发执行**（例如同时 `view_topk` 和一次多点 `guess`）；
- 单个 `guess` 调用本身可一次传入最多 64 个词。

## 后端 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agent/race` | 创建对战（body: `target_word?`, `max_steps?`） |
| POST | `/api/agent/race/{id}/start` | 启动 Agent |
| GET | `/api/agent/race/{id}` | 对战快照 |
| POST | `/api/agent/race/{id}/guess` | 人类猜词 |
| POST | `/api/agent/race/{id}/giveup` | 人类放弃（结算仍等 Agent 结束） |
| GET | `/api/agent/race/{id}/events` | SSE 事件流（思考 / 工具调用 / 结果 / 结束） |

SSE 事件类型：`snapshot` `start` `assistant` `tool_result` `guess_table` `human_guess` `human_done` `finish` `agent_done` `race_end` `error`。

## 环境变量（`.env`，不入库）

```
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_PROXY=            # 留空=直连；也可填 http://127.0.0.1:7890
SEMANTLE_ENGINE=local      # 用真实词向量，任意词都有精确相似度
```

## 运行

```bash
./run-agent.sh start     # 后端 :8001, 前端 :5174
./run-agent.sh log       # 看后端日志
./run-agent.sh stop
```

依赖与原版一致，额外需要 `httpx`（DeepSeek 客户端）与 `gensim`（local 引擎）。

## 代码位置

```
backend/app/agent/
├── deepseek.py   # DeepSeek Chat Completions 客户端（支持 tools）
├── tools.py      # guess / view_topk 两个工具的 schema 与执行器
├── runner.py     # Agent 主循环（并行工具调用）
├── race.py       # 对战会话 + SSE 事件队列
└── routes.py     # FastAPI 路由
frontend/race.html / race.css / race.js
```

## 说明

- 对战双方的游戏 `source="shared"`，不写入排行榜。
- 冷门词（不在目标答案 Top-3000 邻居内）rank 显示为 `3000+`。
- Agent 单局上限 `max_steps`（默认 12）个对话回合。
