"""
Agent 可用的两个工具：guess（尝试猜词）与 view_topk（看答案的 Top-K 相似词）。

- guess      : 批量猜词，返回每个词的相似度与排名
- view_topk  : 查看目标答案的 Top-K 邻居（k=-1 表示全部 TOP_N 个）
  两者都天然支持"一次传入多个 / 一次看完"，即并行返回。

对齐需求：结果始终是「一行一词一相似度一rank」。
"""
from __future__ import annotations

import bisect
from typing import Any, Dict, List, Optional, Tuple

from ..game import Game, GameStore

MAX_BATCH = 64  # 单次 guess 最多猜多少个词


# ---------------- 工具 schema（OpenAI function calling 格式） ----------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "guess",
            "description": (
                "尝试猜词。一次可以传入多个词并行提交，系统会返回每个词与隐藏答案的"
                "语义相似度百分比及其排名（rank 越小越接近答案，rank=1 是最接近答案的词）。"
                "猜中答案本身时相似度为 100。多猜几个不同的词能更快定位方向。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "words": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"要猜的中文词列表，最多 {MAX_BATCH} 个，例如 [\"苹果\",\"电脑\"]",
                    }
                },
                "required": ["words"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_topk",
            "description": (
                "查看隐藏答案的 Top-K 最相似词（按相似度降序，含相似度与排名）。"
                "k=-1 表示查看全部（最多 3000 个）。这是最直接的侦察手段："
                "排名越靠前的词离答案越近，可据此推断答案。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "k": {
                        "type": "integer",
                        "description": "查看前 k 个；-1 表示全部（最多 3000 个）。默认 -1。",
                        "default": -1,
                    }
                },
                "required": [],
            },
        },
    },
]


def _sims_desc(game: Game) -> List[float]:
    return [s for _, s in game.top_neighbors]


def _rank_of(game: Game, sim: float) -> int:
    """根据目标答案的 Top-N 相似度分布，估算任意相似度对应的排名（1-based）。"""
    if sim >= 1.0:
        return 1
    sims = _sims_desc(game)
    if not sims:
        return 1
    # 降序列表：统计严格大于 sim 的邻居数，+1 即名次
    pos = bisect.bisect_left([-s for s in sims], -sim)
    return pos + 1


def _fmt_rows(rows: List[Dict[str, Any]]) -> str:
    """把结果格式化成紧凑的每行 'rank\\tword\\tsim%'，省 token 又直观。"""
    lines = ["rank\tword\tsimilarity_pct"]
    for r in rows:
        if r.get("available", True) is False:
            lines.append(f"-\t{r['word']}\t{ r.get('error','不在词表')}")
        else:
            lines.append(f"{r.get('rank','>')}\t{r['word']}\t{r['similarity_pct']:.2f}")
    return "\n".join(lines)


class ToolExecutor:
    """把工具调用绑定到某一局 Game 上。"""

    def __init__(self, store: GameStore, game: Game) -> None:
        self.store = store
        self.game = game

    # -------- 工具实现 --------

    def guess(self, words: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        seen = set()
        guessed = {r.word for r in self.game.history}
        for raw in words or []:
            w = (raw or "").strip()
            if not w or w in seen:
                continue
            seen.add(w)
            if not self.store.engine.has(w):
                rows.append({"word": w, "available": False, "error": "不在词表"})
                continue
            if w in guessed:
                # 已经猜过：直接复用最近一次结果，不重复写历史
                prev = next((r for r in reversed(self.game.history) if r.word == w), None)
                if prev is not None:
                    rows.append({
                        "word": w,
                        "similarity_pct": prev.similarity_pct,
                        "rank": prev.proximity_rank or _rank_of(self.game, prev.similarity),
                        "level": prev.proximity_level,
                        "is_target": prev.is_target,
                        "source": "guess",
                    })
                    continue
            rec = self.store.guess(self.game, w)
            rank = rec.proximity_rank or _rank_of(self.game, rec.similarity)
            rows.append({
                "word": w,
                "similarity_pct": rec.similarity_pct,
                "similarity": rec.similarity,
                "rank": rank,
                "level": rec.proximity_level,
                "is_target": rec.is_target,
                "source": "guess",
            })
        return rows

    def view_topk(self, k: int = -1) -> List[Dict[str, Any]]:
        if k is None:
            k = -1
        top = self.game.top_neighbors
        take = len(top) if k is None or k < 0 else min(int(k), len(top))
        rows: List[Dict[str, Any]] = []
        for i, (w, s) in enumerate(top[:take]):
            rows.append({
                "word": w,
                "similarity_pct": round(s * 100, 2),
                "similarity": round(s, 6),
                "rank": i + 1,
                "source": "topk",
            })
        return rows

    # -------- 统一入口 --------

    def execute(self, name: str, args: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        if name == "guess":
            words = args.get("words") or []
            if isinstance(words, str):
                words = [words]
            rows = self.guess(list(words)[:MAX_BATCH])
            return _fmt_rows(rows), rows
        if name == "view_topk":
            k = args.get("k", -1)
            try:
                k = int(k)
            except (TypeError, ValueError):
                k = -1
            rows = self.view_topk(k)
            header = f"# 目标答案的 Top-{len(rows)} 相似词（输入词数 {len(rows)}）\n"
            return header + _fmt_rows(rows), rows
        raise ValueError(f"未知工具：{name}")
