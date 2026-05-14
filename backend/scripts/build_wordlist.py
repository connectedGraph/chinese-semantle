"""
生成谜底候选词表 target_words.txt（极简版）

设计原则
--------
本脚本采用**纯白名单**机制：data/whitelist.txt 是人工维护的源文件，
本脚本只做三道硬校验，将白名单编译为 data/target_words.txt 供 engine.py 加载。

不再涉及 jieba / 词性 / 黑名单过滤——白名单维护者对内容负责，
脚本只是机械校验与翻译。

三道硬校验
----------
1. 长度 ∈ [MIN_LEN, MAX_LEN]   (默认 2 ~ 4，太长玩家无法完整猜中)
2. 纯中文字符                  (避免英文/数字/标点混入)
3. 必须存在于腾讯 AI Lab embedding 中  (运行期相似度计算的硬条件)

自验收
------
- PROBE_LIVING / PROBE_NEW / PROBE_PROPER 高频日常词 / 品牌 / 专名应在白名单中（命中率达标）
- PROBE_NEGATIVE_HARD（成语 / 政治军事复合词 / 用户吐槽词）必须 0 命中

输出格式
--------
首行 `# generated at <timestamp>` 注释；其后一行一词。
engine.py 加载时跳过 `#` 与空行。

用法
----
    cd backend
    ./.venv/bin/python scripts/build_wordlist.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine import get_engine  # noqa: E402

DATA_DIR = ROOT / "data"
WHITELIST_PATH = DATA_DIR / "whitelist.txt"
OUT_PATH = DATA_DIR / "target_words.txt"

MIN_LEN, MAX_LEN = 2, 4

# ---------- 自验收探针 ----------
# 这些词应该都在白名单中（覆盖性达标）
PROBE_LIVING = (
    "海洋 知识 风扇 西瓜 饺子 山脉 鼠标 冰箱 国画 春节 老虎 沙滩 "
    "可乐 邮件 歌手 火锅 云朵 明星 戈壁 西装 荔枝 耳机 炒饭 简历 烧烤 花朵 "
    "冰棒 沙发 鸽子 溶洞 玫瑰 草莓 月饼 奶茶 排球 树叶 香蕉 猴子 贴纸 "
    "洗衣机 二维码 贝壳 牙签 救护车 雪糕 绿茶 钱包 卫星 白银 荷花 蛋糕 蜘蛛 "
    "短裤 纸巾 煎蛋 温泉 茶几 生肖 水枪 乒乓球 摩天轮 烟花 鼻孔 "
    "巧克力 三角洲 农药 唐朝 键盘 灯泡 体育馆 原子弹 香水 废墟 演唱会 柠檬"
).split()
PROBE_NEW = "微信 抖音 淘宝 腾讯 华为 京东 美团 无人机 机器人 海底捞 特斯拉 二次元".split()
PROBE_PROPER = "深圳 纽约 浙江 韩国 达芬奇 孔子 秦始皇 孙悟空 姚明".split()

# 这些词必须**不**出现在白名单中（白名单维护红线）
PROBE_NEGATIVE_HARD = (
    # 成语
    "目瞪口呆 不可思议 自然而然 拨乱反正 实事求是 全神贯注 迄今为止 当家作主 "
    # 网络流行短语 / 非名词
    "盘他 绝绝子 有史以来 好不容易 大不了 不得了 "
    # 政治 / 军事 / 政体复合词
    "中央政府 政府部门 党政军 工农兵 抗美援朝 一国两制 "
    # 用户最新吐槽的 27 词
    "全国人大 国民经济 总人口 中国足协 美国空军 复旦大学 地球化学 二氯 联苯 黄骅 "
    "抗逆性 旅游胜地 大专院校 审时度势 信息系统 普普通通 发号施令 官兵们 聪明人 "
    "超额利润 南开大学 技术性 形形色色 医疗保险 声东击西 商品流通 中国银行"
).split()


def is_pure_chinese(s: str) -> bool:
    return bool(s) and all("\u4e00" <= ch <= "\u9fff" for ch in s)


def load_whitelist(path: Path) -> list[str]:
    """读取白名单文件，保留顺序，跳过 # 与空行；返回有效词列表（顺序去重）。"""
    seen: set[str] = set()
    ordered: list[str] = []
    if not path.exists():
        sys.exit(f"❌ 白名单文件不存在: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        w = s.split()[0]
        if w in seen:
            continue
        seen.add(w)
        ordered.append(w)
    return ordered


def main() -> None:
    print("[1/4] 加载白名单")
    raw = load_whitelist(WHITELIST_PATH)
    print(f"      whitelist.txt: {len(raw)} 个有效词")

    print("[2/4] 加载 embedding")
    engine = get_engine()
    has_in_kv = engine.has

    print("[3/4] 三道硬校验")
    kept: list[str] = []
    rejected = {"len": [], "non_cn": [], "not_in_kv": []}
    for w in raw:
        if not (MIN_LEN <= len(w) <= MAX_LEN):
            rejected["len"].append(w); continue
        if not is_pure_chinese(w):
            rejected["non_cn"].append(w); continue
        if not has_in_kv(w):
            rejected["not_in_kv"].append(w); continue
        kept.append(w)

    for reason, words in rejected.items():
        if words:
            sample = words[:20]
            more = f"... (+{len(words) - 20} more)" if len(words) > 20 else ""
            print(f"      [REJECT/{reason}] {len(words)} 词: {sample}{more}")

    print(f"      校验通过: {len(kept)} / {len(raw)}")

    print("[4/4] 写出 target_words.txt")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with OUT_PATH.open("w", encoding="utf-8") as f:
        f.write(f"# generated at {timestamp} by scripts/build_wordlist.py\n")
        f.write(f"# source: data/whitelist.txt  total: {len(kept)} words\n")
        for w in kept:
            f.write(f"{w}\n")
    print(f"✓ 已写入 {OUT_PATH}  ({len(kept)} 词)")

    # 自验收
    print()
    print("=" * 60)
    print("自验收")
    print("=" * 60)
    final = set(kept)
    ok = True

    def check_recall(label: str, probe: list[str], min_recall: float) -> None:
        nonlocal ok
        hit = [w for w in probe if w in final]
        miss = [w for w in probe if w not in final]
        recall = len(hit) / len(probe) if probe else 1.0
        status = "PASS" if recall >= min_recall else "FAIL"
        print(f"  [{status}] {label}: {len(hit)}/{len(probe)} = {recall:.1%}（要求 ≥ {min_recall:.0%}）")
        if miss:
            print(f"         miss: {miss}")
        if recall < min_recall:
            ok = False

    def check_negative(label: str, probe: list[str]) -> None:
        nonlocal ok
        hit = [w for w in probe if w in final]
        status = "PASS" if not hit else "FAIL"
        print(f"  [{status}] {label}: 命中 {len(hit)} 个不适宜词（要求 = 0）")
        if hit:
            print(f"         残留: {hit}")
            ok = False

    check_recall("生活高频词覆盖", PROBE_LIVING, 0.95)
    check_recall("新词品牌覆盖", PROBE_NEW, 0.90)
    check_recall("接受范围内的专名", PROBE_PROPER, 0.85)
    check_negative("成语/网络残词/政治军事/用户吐槽词（硬边界）", PROBE_NEGATIVE_HARD)

    print()
    if ok:
        print("✓ 自验收全部通过")
    else:
        print("⚠️  自验收未通过，请调整 data/whitelist.txt 后重跑")
        sys.exit(1)


if __name__ == "__main__":
    main()
