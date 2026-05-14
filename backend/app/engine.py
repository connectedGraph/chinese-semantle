"""
词向量引擎 —— 封装 gensim + 腾讯 AI Lab 中文词向量。

设计要点
--------
1. 单例模式：全进程共享同一份词向量，避免重复加载。
2. 二进制缓存：首次从 .txt 加载后，自动保存为 .kv 二进制格式，下次秒级加载。
3. 懒加载：模块导入时不加载，第一次 get_engine() 时才真正加载。
4. 接口最小化：similarity(a, b)、top_k(word, k)、has(word)、random_word()。
   未来若替换为 BGE / M3E，只需保持这几个方法签名即可。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

# gensim 是 LocalEngine 的硬依赖，但本地 dev 机器上可能没有；
# 改为惰性导入：仅在 _load_engine 被实际调用时才 import，
# 这样 _MockEngine 在缺 gensim 时也能用作 fallback。
KeyedVectors: Any = None  # 延后赋值

logger = logging.getLogger(__name__)

# 项目根目录的 backend/data
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 词向量源文件候选。.bin 视为 word2vec C 二进制格式；.txt 视为文本格式。
# 任意一个存在即可，按列表顺序优先匹配。
RAW_CANDIDATES = [
    "light_Tencent_AILab_ChineseEmbedding.bin",
    "tencent_embedding.bin",
    "tencent_embedding.txt",
    "tencent-ailab-embedding.txt",
    "light_Tencent_AILab_ChineseEmbedding.txt",
]
# 二进制缓存文件名（gensim KeyedVectors 自有格式，非 word2vec C bin）
BINARY_CACHE = "embedding.kv"

# 高频谜底白名单文件（由 scripts/build_wordlist.py 离线生成）
# 每行形如 "词\t词频\t词性"；也兼容"一行一词"的极简格式。
TARGET_POOL_FILE = "target_words.txt"


class WordVectorEngine:
    """中文词向量引擎。"""

    def __init__(self, kv: Any) -> None:
        self._kv = kv
        # 缓存全部词表（玩家猜词路径仍使用全词表）
        self._all_words: List[str] = list(kv.key_to_index.keys())
        # 高频谜底候选池：仅用于 random_word() 的默认采样源
        # 词表缺失 / 为空时 _target_pool 为 []，random_word 会自动回落到 _all_words。
        self._target_pool: List[str] = self._load_target_pool()
        # 高频池的 set 镜像，供 is_common() 做 O(1) 查询（例如提示词过滤）
        self._target_pool_set: set = set(self._target_pool)
        logger.info("Engine ready: %d words, dim=%d", len(self._all_words), kv.vector_size)

    # ------- 谜底候选池加载 -------

    def _load_target_pool(self) -> List[str]:
        """
        加载高频谜底白名单（由 build_wordlist.py 构建）。
        - 只取每行第一列（词本身），兼容 "词\t词频\t词性" 和 "一行一词" 两种格式
        - 自动跳过 # 注释行与空行（构建脚本会写入 # generated at ... 元信息）
        - 自动过滤不在词向量中的词，保证所有谜底都能算相似度 / Top-K
        - 文件缺失或为空时返回 [] 并 WARNING，运行期将回落到全词表（向后兼容）
        """
        path = DATA_DIR / TARGET_POOL_FILE
        if not path.exists():
            logger.warning(
                "Target pool file not found: %s. "
                "Falling back to full vocabulary for random_word() "
                "(run scripts/build_wordlist.py to generate it).",
                path,
            )
            return []
        pool: List[str] = []
        skipped = 0
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # 只取第一列：优先按 TAB 切（构建脚本输出格式），再按空白兜底
                    word = line.split("\t", 1)[0].split()[0].strip()
                    if not word:
                        continue
                    if word not in self._kv.key_to_index:
                        # 词表里有但词向量没有的词（理论上构建脚本已过滤，这里做二次防线）
                        skipped += 1
                        continue
                    pool.append(word)
        except OSError as e:
            logger.warning("Failed to read target pool %s: %s; fallback to full vocab.", path, e)
            return []

        if not pool:
            logger.warning("Target pool is empty after filtering; fallback to full vocab.")
            return []
        logger.info(
            "Target pool loaded: %d words from %s%s",
            len(pool), TARGET_POOL_FILE,
            f" (skipped {skipped} not in vectors)" if skipped else "",
        )
        return pool

    # ------- 基础查询 -------

    def has(self, word: str) -> bool:
        return word in self._kv.key_to_index

    def is_common(self, word: str) -> bool:
        """判断 word 是否在高频池内（O(1)）。池为空时恒返回 False。"""
        return word in self._target_pool_set

    @property
    def has_target_pool(self) -> bool:
        """是否启用了高频池。未启用时调用方应回落到原行为。"""
        return bool(self._target_pool_set)

    def similarity(self, w1: str, w2: str) -> float:
        """返回 [-1, 1] 的余弦相似度；任一词不存在则返回 -1。"""
        if not self.has(w1) or not self.has(w2):
            return -1.0
        return float(self._kv.similarity(w1, w2))

    def top_k(self, word: str, k: int = 1000) -> List[Tuple[str, float]]:
        """返回 word 的 Top-K 最相似邻居（不含自身），按相似度降序。"""
        if not self.has(word):
            return []
        # most_similar 默认就不含 word 本身
        return [(w, float(s)) for w, s in self._kv.most_similar(word, topn=k)]

    # ------- 选词 -------

    def random_word(
        self,
        min_len: int = 2,
        max_len: int = 4,
        candidates: Optional[List[str]] = None,
    ) -> str:
        """
        随机抽一个目标词。

        采样源优先级：
          1. 显式传入的 candidates（调用方可临时指定白名单，语义不变）
          2. self._target_pool（高频日常词谜底池，来自 target_words.txt）
          3. self._all_words（全词表，仅在以上两者皆为空时回落）
        """
        if candidates:
            pool = candidates
        elif self._target_pool:
            pool = self._target_pool
        else:
            pool = self._all_words

        # 简单过滤：纯中文 + 长度在 [min_len, max_len]
        for _ in range(50):  # 最多重试 50 次
            w = random.choice(pool)
            if min_len <= len(w) <= max_len and _is_pure_chinese(w):
                return w
        # 兜底
        return random.choice(pool)

    @property
    def vocab_size(self) -> int:
        return len(self._all_words)


def _is_pure_chinese(s: str) -> bool:
    """简单判定是否纯中文（剔除英文、数字、标点等）。"""
    if not s:
        return False
    return all("\u4e00" <= ch <= "\u9fff" for ch in s)


# -------------------- 单例加载 --------------------

_engine: Optional[WordVectorEngine] = None
_lock = threading.Lock()


def get_engine() -> WordVectorEngine:
    """全局单例。线程安全。"""
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            _engine = _load_engine()
    return _engine


def _load_engine() -> WordVectorEngine:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    binary_path = DATA_DIR / BINARY_CACHE

    # 惰性导入 gensim：缺依赖时让上层 fallback 到 MockEngine
    global KeyedVectors
    if KeyedVectors is None:
        from gensim.models import KeyedVectors as _KV  # noqa: F401  type: ignore
        KeyedVectors = _KV

    # 优先加载二进制缓存
    if binary_path.exists():
        logger.info("Loading binary cache: %s", binary_path)
        t0 = time.time()
        kv = KeyedVectors.load(str(binary_path), mmap="r")
        logger.info("Loaded in %.2fs", time.time() - t0)
        return WordVectorEngine(kv)

    # 否则从原始文件加载并落盘缓存
    raw_path = _find_raw_file()
    if raw_path is None:
        raise FileNotFoundError(
            f"No word vector file found in {DATA_DIR}. "
            f"Expected one of: {RAW_CANDIDATES}.\n"
            f"Please download from: https://ai.tencent.com/ailab/nlp/zh/embedding.html"
        )

    is_binary = raw_path.suffix.lower() == ".bin"
    fmt = "word2vec-binary" if is_binary else "word2vec-text"
    logger.info("Loading raw vectors from: %s (format=%s)", raw_path, fmt)
    t0 = time.time()
    kv = KeyedVectors.load_word2vec_format(str(raw_path), binary=is_binary)
    logger.info("Loaded raw in %.2fs (vocab=%d, dim=%d)",
                time.time() - t0, len(kv.key_to_index), kv.vector_size)

    logger.info("Saving binary cache to: %s", binary_path)
    kv.save(str(binary_path))
    logger.info("Binary cache saved. Next startup will be fast.")

    return WordVectorEngine(kv)


def _find_raw_file() -> Optional[Path]:
    for name in RAW_CANDIDATES:
        p = DATA_DIR / name
        if p.exists():
            return p
    # 兜底：扫一下 data 目录里所有 .bin / .txt 文件
    for ext in ("*.bin", "*.txt"):
        for p in DATA_DIR.glob(ext):
            return p
    return None


# -------------------- 测试模式 --------------------

class _MockEngine(WordVectorEngine):
    """
    无词向量文件 / 缺 gensim 时的 mock 引擎，仅用于本地联调前端。
    用 hash 模拟相似度，可以让接口跑起来但分数无意义。
    """

    def __init__(self) -> None:
        # 内置一个小词表用于演示。**必须包含 daily_challenges.yaml 中配置的所有词**，
        # 否则本地 dev 跑每日挑战会因 engine.has() 返回 False 而创建失败。
        self._mock_vocab = [
            # 食物
            "苹果", "香蕉", "橘子", "葡萄", "西瓜", "草莓", "菠萝", "芒果",
            "米饭", "面条", "馒头", "饺子", "汤圆", "豆浆", "油条", "煎饼",
            # 数码
            "电脑", "手机", "键盘", "鼠标", "屏幕", "耳机", "音箱", "充电",
            # 动物
            "猫咪", "狗狗", "兔子", "老虎", "狮子", "熊猫", "鸟儿", "鱼儿",
            # 情绪
            "快乐", "悲伤", "愤怒", "平静", "兴奋", "疲惫", "幸福", "孤独",
            # 地名
            "中国", "北京", "上海", "广州", "深圳", "杭州", "成都", "重庆",
            # 行为
            "学习", "工作", "休息", "运动", "阅读", "写作", "思考", "创造",
            # 自然 / daily 配置覆盖
            "夜空", "星辰", "月亮", "太阳", "云朵", "微风", "湖泊", "江河",
            "森林", "沙滩", "黄昏", "晴天", "山水",
        ]
        self._all_words = list(self._mock_vocab)
        self._target_pool: List[str] = []
        self._target_pool_set: set = set()
        logger.warning("Running in MOCK mode. Similarity scores are NOT real.")

    def has(self, word: str) -> bool:
        # 接受 mock_vocab 中的词，或任意 2-4 字纯中文词（让玩家能猜任何东西）
        if word in self._mock_vocab:
            return True
        return 2 <= len(word) <= 4 and _is_pure_chinese(word)

    def similarity(self, w1: str, w2: str) -> float:
        if w1 == w2:
            return 1.0
        h = hash((min(w1, w2), max(w1, w2))) % 10000
        return -0.3 + (h / 10000) * 1.25

    def top_k(self, word: str, k: int = 1000) -> List[Tuple[str, float]]:
        scored = [(w, self.similarity(word, w)) for w in self._mock_vocab if w != word]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    def random_word(self, min_len: int = 2, max_len: int = 4,
                    candidates: Optional[List[str]] = None) -> str:
        return random.choice(self._mock_vocab)


def get_engine_or_mock() -> WordVectorEngine:
    """生产用 get_engine，词向量 / gensim 缺失时回落到 mock 以便联调前端。"""
    try:
        return get_engine()
    except (FileNotFoundError, ImportError, ModuleNotFoundError) as e:
        logger.warning(
            "Real engine unavailable (%s). Falling back to MOCK engine.", e,
        )
        global _engine
        _engine = _MockEngine()
        return _engine
