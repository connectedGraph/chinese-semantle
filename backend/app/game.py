"""
游戏会话管理。

核心数据结构
------------
Game:
  - id, target_word, created_at, is_finished
  - top_neighbors: List[(word, sim)]  目标词的 Top-1000 邻居（按 sim 降序）
  - rank_map:      Dict[word, rank]   word -> 排名 (1-based)，O(1) 查询
  - history:       List[GuessRecord]  按时间顺序的猜词历史
  - hints:         List[HintWord]     创建时给出的 3 个引导词

接近度分档
----------
- hot:  rank ∈ [1, 300]
- warm: rank ∈ [301, 1000]
- cold: 不在 Top-1000

未来若要落 Redis：把下面的 _games 字典换成 RedisGameStore 即可。
"""

from __future__ import annotations

import logging
import random
import threading
import uuid
from datetime import date, datetime, timezone
from typing import Dict, List, Literal, Optional, Tuple

from .engine_base import EngineProtocol
from .models import GuessRecord, HintWord, ProximityLevel
from .puzzle_codes import encode_word


GameSource = Literal["random", "daily", "shared"]
"""
计分判定的唯一依据：
- random : 通过「新建随机游戏」开局；唯一可计分来源（额外要求未提示、未放弃）
- daily  : 通过「每日挑战 / 回溯」入口开局；永远不计分
- shared : 通过 ?game=XXXXXX 链接进入；永远不计分
"""

logger = logging.getLogger(__name__)

TOP_N = 1000          # 接近度计算的总宽度
HOT_THRESHOLD = 300   # rank ≤ 300 视为 hot
MAX_EXTRA_HINTS = 5   # 每局最多支持手动提示 5 次

# ---- 提示阶梯参数 ----
# 设计意图：5 次提示作为一条等比阶梯，从 HINT_INITIAL_RANK 平滑收敛到 HINT_FINAL_RANK
HINT_INITIAL_RANK = 1000   # 玩家毫无线索时，第 1 次提示的起点档位
HINT_FINAL_RANK   = 50     # 玩家暂未触达 Top-50 时，提示阶梯的常规收敛地板
HINT_HARD_FLOOR   = 2      # 绝对地板：rank=1 = 目标本身，永不作为提示
HINT_TOLERANCE    = 0.15   # 实际选词时允许偏离目标 rank 的相对误差 ±15%

# 初始提示词的相似度档位：分别从 (低, 中, 较高) 区间各抽一个，给玩家温度感
HINT_BUCKETS = [
    ("cold", 0.0, 0.15),     # 远但不无关
    ("warm", 0.20, 0.35),    # 中等
    ("warm", 0.35, 0.50),    # 较近但不剧透
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _level_of(rank: Optional[int]) -> ProximityLevel:
    if rank is None:
        return "cold"
    if rank <= HOT_THRESHOLD:
        return "hot"
    return "warm"


class Game:
    def __init__(
        self,
        game_id: str,
        target: str,
        top_neighbors: List[Tuple[str, float]],
        hints: List[HintWord],
        puzzle_code: Optional[str] = None,
        source: GameSource = "random",
        daily_date: Optional[date] = None,
    ) -> None:
        self.id = game_id
        self.target = target
        self.puzzle_code = puzzle_code or encode_word(target)
        self.created_at = _now()
        self.is_finished = False
        self.top_neighbors = top_neighbors
        self.rank_map: Dict[str, int] = {
            w: i + 1 for i, (w, _) in enumerate(top_neighbors)
        }
        self.sim_map: Dict[str, float] = {w: s for w, s in top_neighbors}
        self.history: List[GuessRecord] = []
        self.hints = hints
        self.extra_hint_count: int = 0  # 已使用的手动提示次数
        # 一旦使用过提示，本局成绩永久失去排行榜资格（即使后续不再用，也不能重置）
        self.hint_ever_used: bool = False
        # 一旦放弃过，本局成绩永久失去排行榜资格
        self.give_up_ever: bool = False
        # 来源 / 每日挑战所属日期（仅 source == "daily" 时非空）
        self.source: GameSource = source
        self.daily_date: Optional[date] = daily_date
        self._lock = threading.Lock()

    @property
    def guess_count(self) -> int:
        return len(self.history)

    @property
    def is_scoring(self) -> bool:
        """
        本局是否计入排行榜。**计分判定的唯一中心**。

        规则：
            source == "random" AND not hint_ever_used AND not give_up_ever
        """
        return (
            self.source == "random"
            and not self.hint_ever_used
            and not self.give_up_ever
        )

    @property
    def scoring_reason(self) -> str:
        """
        非计分原因（用于前端 hover tooltip 第一段）。
        计分时返回固定的肯定文案。
        """
        if self.is_scoring:
            return "本局成绩可提交到随机游戏排行榜"
        # 优先级：分享/每日（来源） > 已使用提示 > 已放弃后继续
        if self.source != "random":
            return "非随机游戏"
        if self.hint_ever_used:
            return "已使用提示"
        if self.give_up_ever:
            return "已放弃后继续猜词"
        return "非计分局"


class GameStore:
    """内存版游戏仓库。线程安全。"""

    def __init__(self, engine: "EngineProtocol") -> None:
        self.engine = engine
        self._games: Dict[str, Game] = {}
        self._lock = threading.Lock()

    # -------- 创建 --------

    def create_game(
        self,
        hint_count: int = 0,
        min_word_len: int = 2,
        max_word_len: int = 4,
        target_word: Optional[str] = None,
        source: GameSource = "random",
        daily_date: Optional[date] = None,
    ) -> Game:
        """
        创建一局游戏。
        - 若提供 target_word：以指定词作为谜底（用于「按 puzzle_code 进入指定一局」
          或「每日挑战」）；需要 engine.has(target_word) 为真，否则抛 ValueError。
        - 否则按长度区间随机抽一个谜底。

        source / daily_date 仅用于排行榜计分判定，不影响游戏玩法本身。
        """
        if target_word:
            if not self.engine.has(target_word):
                raise ValueError(f"目标词不在词表中：{target_word}")
            target = target_word
        else:
            target = self._pick_target(min_word_len, max_word_len)
        logger.info(
            "New game target picked: %s (len=%d, source=%s, daily=%s)",
            target, len(target), source, daily_date,
        )

        # 预计算 Top-N 邻居（这一步是 Semantle 体验的关键）
        top_neighbors = self.engine.top_k(target, k=TOP_N)

        # 初始提示词；默认不生成（hint_count=0），保留参数兼容老调用方
        hints = (
            self._make_hints(target, top_neighbors, count=hint_count)
            if hint_count > 0 else []
        )

        game_id = uuid.uuid4().hex[:12]
        game = Game(
            game_id, target, top_neighbors, hints,
            source=source, daily_date=daily_date,
        )

        with self._lock:
            self._games[game_id] = game
        return game

    def get(self, game_id: str) -> Optional[Game]:
        return self._games.get(game_id)

    # -------- 猜词 --------

    def guess(
        self,
        game: Game,
        word: str,
        player_name: Optional[str] = None,
    ) -> GuessRecord:
        word = word.strip()
        if not self.engine.has(word):
            raise ValueError(f"词向量中不存在该词：{word}")

        with game._lock:
            sim = self.engine.similarity(game.target, word)
            rank = game.rank_map.get(word)  # O(1)
            level: ProximityLevel = _level_of(rank)
            is_target = (word == game.target)

            record = GuessRecord(
                order=game.guess_count + 1,
                word=word,
                similarity=round(sim, 6),
                similarity_pct=round(sim * 100, 2),
                proximity_rank=rank,
                proximity_level=level,
                is_target=is_target,
                player_name=player_name,
                guessed_at=_now(),
            )
            game.history.append(record)
            if is_target:
                game.is_finished = True
        return record

    # -------- 手动提示 --------

    def request_hint(self, game: Game) -> GuessRecord:
        """
        给玩家一个新的提示词。**等价于自动完成一次猜词**。

        阶梯式逼近算法
        --------------
        把"5 次提示"看作一条等比阶梯路径，把当前最佳 rank 平滑收敛到目标地板：
            next_rank = current_rank * (floor / current_rank) ** (1 / remaining)

        其中
        - remaining = MAX_EXTRA_HINTS - extra_hint_count（包含当前这次）
        - floor = HINT_FINAL_RANK (=50) 当 current_rank > 50 时
                = HINT_HARD_FLOOR (=2)  当 current_rank ≤ 50 时
                  —— 玩家已经凭实力突破前 50 名时，提示功能继续等比缩半逼近
                  到 #2，而不是因为撞到原来的"50 地板"而失效。

        效果：
        - 没猜过任何词时，5 次提示路径约为 #1000 → #549 → #302 → #166 → #91 → #50
        - 用户已经猜到 #100 时，5 次路径约为 #100 → #87 → #76 → #66 → #57 → #50
        - 用户已经猜到 #29 时（关键修复点）：路径约为 #29 → #20 → #14 → #10 → #7 → #5
          —— 不再因为撞到原 50 地板而抛"没有可用的新提示词"
        - 提示词的 rank 永远 < current_rank（严格优于已知最佳）

        防剧透：保留 rank=1（目标自身）不会作为提示。
        """
        if game.is_finished:
            raise ValueError("本局游戏已结束")

        with game._lock:
            if game.extra_hint_count >= MAX_EXTRA_HINTS:
                raise ValueError(f"提示已用完（最多 {MAX_EXTRA_HINTS} 次）")
            if not game.top_neighbors:
                raise ValueError("当前游戏没有可用的提示候选")

            # 1. 收集已知词
            known_words = {r.word for r in game.history}
            known_words.update(h.word for h in game.hints)
            known_words.add(game.target)

            # 2. 当前最佳 rank：已知词中最靠前的；都没在 Top-1000 内则视作 HINT_INITIAL_RANK
            ranks = [game.rank_map[w] for w in known_words
                     if w in game.rank_map and w != game.target]
            current_rank = min(ranks) if ranks else HINT_INITIAL_RANK

            # 已经触达 #2（除目标自身以外的最靠前），无法再前进
            if current_rank <= HINT_HARD_FLOOR:
                raise ValueError("已经非常接近答案，无需更多提示了")

            # 3. 等比阶梯：本次目标 rank
            target_rank = self._next_hint_rank(
                current_rank=current_rank,
                remaining=MAX_EXTRA_HINTS - game.extra_hint_count,
            )

            # 4. 在 Top-1000 中找最接近 target_rank 的、未出现过的、严格更近的词
            best_word = self._select_hint_word(
                game=game,
                target_rank=target_rank,
                current_rank=current_rank,
                known_words=known_words,
            )
            if best_word is None:
                raise ValueError("没有可用的新提示词")

            # 5. 写入 history（is_hint=True 标记）
            sim = game.sim_map[best_word]
            rank = game.rank_map[best_word]
            level = _level_of(rank)
            record = GuessRecord(
                order=game.guess_count + 1,
                word=best_word,
                similarity=round(sim, 6),
                similarity_pct=round(sim * 100, 2),
                proximity_rank=rank,
                proximity_level=level,
                is_target=False,
                is_hint=True,
                player_name=None,
                guessed_at=_now(),
            )
            game.history.append(record)
            game.extra_hint_count += 1
            game.hint_ever_used = True
            logger.info(
                "Hint #%d: current_rank=%d → target_rank=%d → picked '%s' (rank=%d)",
                game.extra_hint_count, current_rank, target_rank, best_word, rank,
            )
        return record

    @staticmethod
    def _next_hint_rank(current_rank: int, remaining: int) -> int:
        """
        计算阶梯下一步的目标 rank。等比收敛到动态地板：
        - current_rank > HINT_FINAL_RANK：地板 = HINT_FINAL_RANK (50)
        - current_rank ≤ HINT_FINAL_RANK：地板 = HINT_HARD_FLOOR (2)
          —— 玩家已突破 #50 时，提示继续等比缩半逼近，不再因撞 50 地板而失效
        """
        if current_rank <= HINT_HARD_FLOOR:
            return HINT_HARD_FLOOR  # 已是最近，理论上调用方已拦截

        floor = HINT_FINAL_RANK if current_rank > HINT_FINAL_RANK else HINT_HARD_FLOOR

        if remaining <= 1:
            # 最后一次提示：直接给到地板
            return max(floor, HINT_HARD_FLOOR)

        # 等比缩短：n 步缩到地板
        shrink = (floor / current_rank) ** (1.0 / remaining)
        nxt = max(floor, round(current_rank * shrink))
        # 至少前进 1 步
        return min(nxt, current_rank - 1)

    def _select_hint_word(
        self,
        game: "Game",
        target_rank: int,
        current_rank: int,
        known_words: set,
    ) -> Optional[str]:
        """
        在 Top-1000 邻居中挑一个：
        - rank 落在 [target_rank * (1-tol), target_rank * (1+tol)] 区间内
        - rank 严格 < current_rank（必须比已知最佳更接近）
        - rank ≥ HINT_HARD_FLOOR（绝对地板：rank=1 是目标自身，永不外泄）
        - 未出现在已知词集中
        - **优先从"高频日常词池"中选**（engine.is_common），避免把 "李小姐" / "条路"
          这种长尾专名 / 残分词当作提示；若高频池内无可用词，再回落到全邻居。
        命中多个时，选**最接近 target_rank 但不低于 target_rank 的**（宁可稍远不剧透）；
        都没命中时退化到"全局最接近 target_rank 的合规词"。
        """
        tol = max(1, int(target_rank * HINT_TOLERANCE))
        lo = max(HINT_HARD_FLOOR, target_rank - tol)
        hi = min(current_rank - 1, target_rank + tol)

        # 双通道：[0] = 仅高频池候选；[1] = 全邻居候选（作为回退）
        in_zone: List[Optional[str]] = [None, None]
        in_zone_diff: List[float] = [float("inf"), float("inf")]
        fallback: List[Optional[str]] = [None, None]
        fallback_diff: List[float] = [float("inf"), float("inf")]

        use_pool = self.engine.has_target_pool  # 若未启用高频池，双通道退化为单通道

        for w, _ in game.top_neighbors:
            if w in known_words:
                continue
            rank = game.rank_map[w]
            if rank >= current_rank or rank < HINT_HARD_FLOOR:
                continue
            # 偏好"不比 target 更近"的方向（rank ≥ target_rank），
            # 这样阶梯不会在稀疏档位提前冲过头
            diff = (rank - target_rank) if rank >= target_rank else (target_rank - rank) * 1.5

            # 0 号通道只收高频池内的词；1 号通道全收
            channels = (0, 1) if (use_pool and self.engine.is_common(w)) else (1,)
            for ch in channels:
                if lo <= rank <= hi and diff < in_zone_diff[ch]:
                    in_zone[ch], in_zone_diff[ch] = w, diff
                if diff < fallback_diff[ch]:
                    fallback[ch], fallback_diff[ch] = w, diff

        # 选词优先级：高频池区间内 > 高频池兜底 > 全邻居区间内 > 全邻居兜底
        for candidate in (in_zone[0], fallback[0], in_zone[1], fallback[1]):
            if candidate is not None:
                return candidate
        return None

    # -------- 选词 --------

    def _pick_target(self, min_len: int, max_len: int) -> str:
        """
        选一个有意义的目标词：要求 Top-100 邻居存在足够、长度合适。

        注意：谜底采样源已在 ``WordVectorEngine.random_word`` 内部收敛到
        ``backend/data/target_words.txt`` 的高频日常词白名单（由
        ``scripts/build_wordlist.py`` 离线生成）。本方法仅做"能跑通 Top-K"的
        二次质量校验，不需要改动调用方式；词表缺失时会自动回落到全词表，
        保持向后兼容。

        猜词路径（``engine.similarity`` / ``engine.has`` / ``engine.top_k``）
        与本方法无关，玩家仍可使用全部 14.3 万词向量词。
        """
        for _ in range(20):
            w = self.engine.random_word(min_len=min_len, max_len=max_len)
            # 简单质量检查：能算出 Top-100 邻居才算是"有效目标"
            if self.engine.has(w):
                neighbors = self.engine.top_k(w, k=10)
                if len(neighbors) >= 5:
                    return w
        # 兜底
        return self.engine.random_word(min_len=min_len, max_len=max_len)

    # -------- 初始提示词 --------

    def _make_hints(
        self,
        target: str,
        top_neighbors: List[Tuple[str, float]],
        count: int = 3,
    ) -> List[HintWord]:
        """
        给玩家 3 个引导词作为"温度参考"：
        - 从 Top-1000 的不同区段（低/中/较高）各抽一个
        - 让玩家直观感知到"什么样的相似度对应什么样的接近度"
        - **每个区段内优先挑"高频日常词池"里的词**（engine.is_common），避免
          专名 / 长尾 / 残分词当提示；池内挑不到再从该区段兜底。
        """
        if not top_neighbors or count <= 0:
            return []

        n = len(top_neighbors)
        # 分别从相似度的"较低 / 中等 / 较高"区段取
        # 但避免取到最接近答案的前 50 名（那等于剧透）
        # 区段：[300, 800] 较低、[150, 300] 中、[50, 150] 较高
        zones = [
            (max(300, n // 3 * 2), min(n - 1, 800)),  # 远
            (150, 300),                                # 中
            (50, 150),                                 # 较近但不剧透
        ]
        zones = [(lo, min(hi, n - 1)) for lo, hi in zones if lo < n]

        random.shuffle(zones)
        chosen: List[HintWord] = []
        used_words = set()
        use_pool = self.engine.has_target_pool

        def _pick_in_zone(lo: int, hi: int) -> Optional[Tuple[int, str, float]]:
            """在 [lo, hi] 区间挑一个未用过的词。优先高频池；退化到任意词。"""
            if lo >= hi:
                return None
            # 第一通道：枚举区段内所有高频池候选，再从中随机挑一个
            # （比"20 次随机采样"更可靠：只要区段里有任一高频词就必命中）
            if use_pool:
                commons: List[Tuple[int, str, float]] = [
                    (idx, top_neighbors[idx][0], top_neighbors[idx][1])
                    for idx in range(lo, hi + 1)
                    if top_neighbors[idx][0] not in used_words
                    and top_neighbors[idx][0] != target
                    and self.engine.is_common(top_neighbors[idx][0])
                ]
                if commons:
                    return random.choice(commons)
            # 第二通道：降级为区段内随机挑（当区段里完全没有高频词时）
            for _ in range(10):
                idx = random.randint(lo, hi)
                w, s = top_neighbors[idx]
                if w in used_words or w == target:
                    continue
                return idx, w, s
            return None

        for lo, hi in zones[:count]:
            picked = _pick_in_zone(lo, hi)
            if picked is None:
                continue
            idx, w, s = picked
            used_words.add(w)
            rank = idx + 1
            chosen.append(HintWord(
                word=w,
                similarity_pct=round(s * 100, 2),
                proximity_level=_level_of(rank),
            ))

        # 如果没凑够（小词表场景），从尾部往前扫一遍补足
        # 补足阶段同样优先高频池，找不到再降级
        if len(chosen) < count:
            def _scan_fill(require_common: bool) -> None:
                for idx in range(n - 1, -1, -1):
                    if len(chosen) >= count:
                        return
                    w, s = top_neighbors[idx]
                    if w in used_words or w == target:
                        continue
                    if require_common and use_pool and not self.engine.is_common(w):
                        continue
                    used_words.add(w)
                    chosen.append(HintWord(
                        word=w,
                        similarity_pct=round(s * 100, 2),
                        proximity_level=_level_of(idx + 1),
                    ))
            if use_pool:
                _scan_fill(require_common=True)
            if len(chosen) < count:
                _scan_fill(require_common=False)
        return chosen
