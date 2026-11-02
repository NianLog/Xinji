"""XinjiBench v2：合成的关系记忆基准（中文，种子确定性，带完整 ground truth）。

v2 相比 v1 的关键升级：**现象可组合 + 结构随机化**。每段对话从真实现象
池里抽样组合，不再共用同一剧本骨架：

- ``boundary``   边界下的沉默（README 场景，必含）
- ``lift``       「现在可以聊了」→ 边界解除（组织器需识别自然语言）
- ``update``     「改主意了，不去X了，改去Y」→ 意愿变更 + 旧话题关闭
- ``fulfillment``「答应你的X带来了」→ 承诺兑现（之后不再作为约定出现）
- ``restatement``同一愿望换一种说法重申（派生重复记忆，考验装包去重）
- ``multi-fact`` 一句话带多个事实（自我介绍合并偏好）
- ``resurrection`` 删除 + 强制重建后的复活探针（必含 ×2）
- ``stage``      关系阶段迁移（必含 ×2）

判分 ground truth 随现象联动：解除边界后，时节探针从「不许提」翻转为
「应想起」；兑现承诺后，约定探针从「应想起」翻转为「不许提」——任何
一个环节（识别、折叠、判断）出错都会在这套探针上现形。

语料自检：探针话术不自带答案（复活探针除外——用户提问本来就会引用
被删事实的词，判分时剔除当前输入）、闲聊不污染事实匹配词。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

SEASON_ZH = {"spring": "春天", "summer": "夏天", "autumn": "秋天", "winter": "冬天"}

# 事实素材池（对象词彼此不同，保证暴露匹配互不污染）
WISH_PLACES = [
    ("洱海", "看海"), ("青岛", "看海"), ("敦煌", "看星空"), ("北海道", "看雪"),
    ("京都", "看樱花"), ("呼伦贝尔", "看草原"), ("重庆", "看夜景"), ("长白山", "看雾凇"),
    ("大理", "看苍山"), ("厦门", "看日出"), ("漠河", "看极光"), ("婺源", "看油菜花"),
]
PREFS = ["美式咖啡", "茉莉花茶", "黑巧克力", "酸辣粉", "柠檬茶", "薄荷糖",
         "桂花糕", "椒麻鸡", "热清酒", "冰豆浆"]
STORIES = [("修好了那台旧风扇", "旧风扇"), ("把拼图拼完了", "拼图"),
           ("在楼顶看过流星", "流星"), ("烤糊了一炉饼干", "饼干"),
           ("把帐篷撑在了山顶", "帐篷"), ("钓上来一条大鱼", "大鱼")]
COMMITMENTS = [("下次带一盒桂花糕给你", "桂花糕"), ("周末陪你去逛旧书市场", "旧书市场"),
               ("把那本书读完就讲给你听", "那本书"), ("帮你留意那家店的折扣", "那家店的折扣")]
NAMES = ["小满", "阿澈", "苏叶", "林一", "周雨", "陈默", "叶知秋", "江浅"]

FILLERS = [
    "今天上班有点累，回来就不想动了。",
    "晚上吃了碗面，味道还行。",
    "地铁上人多得挤不上去。",
    "最近在追一部剧，还挺好看的。",
    "周末打算把屋子收拾一下。",
    "同事又让我帮忙带饭，哈哈。",
    "今天走了好多路，腿都酸了。",
    "下单了一个小架子，放东西用。",
    "刚洗完澡，舒服多了。",
    "超市打折，囤了点日用品。",
    "今晚早点睡，明天还要早起。",
    "外面风好大，窗户都在响。",
    "中午点了外卖，等了好久才到。",
    "楼下新开了一家小店，还没进去过。",
    "把阳台的盆栽浇了一遍。",
    "开了一下午会，头都大了。",
    "快递总算到了，等了三天。",
    "晚上泡了脚，准备睡了。",
    "修了个水管，折腾了一晚上。",
    "同事养了盆绿萝，快被养死了。",
    "电梯坏了一整天，爬的楼梯。",
    "换了床新被子，睡得挺踏实。",
    "下载了个记账软件，试试看。",
    "最近老是忘记带钥匙。",
    "路过公园看到有人遛狗。",
    "网购的书到了，还挺厚。",
    "今天太阳不错，晒了被子。",
    "煮了锅粥，配了点咸菜。",
]
AMBIENT = {
    "autumn": ["最近降温了，早晚有点凉。", "秋天风一起，树叶就黄了。"],
    "winter": ["这周好冷，我把厚被子拿出来了。", "今天下霜了，路上都白了。"],
    "spring": ["最近回暖了，路边都开花了。", "春天一到就想出门走走。"],
    "summer": ["这两天好热，出门都要带伞遮阳。", "蚊子又出来了，晚上不敢开窗。"],
}

MONTH_SEASON = {3: "spring", 4: "spring", 5: "spring", 6: "summer", 7: "summer",
                8: "summer", 9: "autumn", 10: "autumn", 11: "autumn",
                12: "winter", 1: "winter", 2: "winter"}


def norm(text: str) -> str:
    """暴露匹配用的规范化：小写、去空白与标点。"""
    return re.sub(r"[\s，。！？!?,.、：；;：'\"“”『』（）()\[\]·…—\-]", "", text).lower()


@dataclass
class Fact:
    """一条剧本事实：内容、话题、暴露匹配词、约束与状态。"""

    key: str
    kind: str          # wish | preference | name | story | commitment
    topic: str
    content: str       # 用户原话核心句
    match: list[str]   # 规范化后的独特匹配子串
    season: Optional[str] = None
    boundary: bool = False
    min_stage: int = 0
    status: str = "active"        # active | superseded | fulfilled

    def exposed_in(self, units: list[str]) -> bool:
        n = [norm(u) for u in units]
        return any(any(norm(m) in u for u in n) for m in self.match)


@dataclass
class Turn:
    """脚本中的一轮：对话文本 + 该轮的判分标注与治理动作。"""

    month: int
    role: str                       # user（bench 只写用户侧原话）
    text: str
    recall: list[str] = field(default_factory=list)      # 应当在上下文里的事实
    forbidden: list[str] = field(default_factory=list)   # 不得在上下文里的事实
    after_deletion: bool = False    # forbidden 探针是否属于「删除后复活」类
    stage: Optional[str] = None     # 该轮之后的关系阶段（ground truth）
    deletions: list[dict[str, Any]] = field(default_factory=list)  # 治理删除（不走对话）
    rebuild: bool = False           # 强制重建派生层（补整理压力测试）
    phenomena: list[str] = field(default_factory=list)   # 该轮涉及的现象标签


@dataclass
class Conversation:
    cid: str
    facts: dict[str, Fact]
    turns: list[Turn]
    fact_source_turn: dict[str, int] = field(default_factory=dict)  # 事实→原话所在轮
    phenomena: list[str] = field(default_factory=list)   # 本段对话包含的现象

    def stats(self) -> dict[str, Any]:
        recall = sum(len(t.recall) for t in self.turns)
        forbidden = sum(1 for t in self.turns if t.forbidden)
        deletions = sum(len(t.deletions) for t in self.turns)
        return {"cid": self.cid, "messages": sum(1 for t in self.turns if t.text),
                "recall_probes": recall, "forbidden_probes": forbidden,
                "deletions": deletions,
                "stage_events": sum(1 for t in self.turns if t.stage)}


def generate_conversation(rng: random.Random, cid: str) -> Conversation:
    """生成一段跨 8 个月的陪伴关系脚本（约 50–70 轮，7–9 个探针）。"""
    name = rng.choice(NAMES)
    (place_a, act_a), (place_b, act_b), (place_c, act_c) = rng.sample(WISH_PLACES, 3)
    story_text, story_obj = rng.choice(STORIES)
    commit_text, commit_obj = rng.choice(COMMITMENTS)
    # 偏好词不得与承诺/故事对象冲突（否则暴露匹配互串）
    safe_prefs = [p for p in PREFS if p not in commit_text and p not in story_text
                  and commit_obj not in p and story_obj not in p]
    pref_keep, pref_del = rng.sample(safe_prefs, 2)

    phenomena = {
        "lift": rng.random() < 0.5,
        "update": rng.random() < 0.5,
        "fulfillment": rng.random() < 0.5,
        "restatement": rng.random() < 0.3,
        "multi-fact": rng.random() < 0.5,
    }
    # restatement 与 update 互斥（都发生在深秋，避免话题纠缠）
    if phenomena["update"]:
        phenomena["restatement"] = False

    wish_a = Fact(
        key="wish_a", kind="wish", topic=f"{place_a}{act_a}",
        content=f"我想在秋天去{place_a}{act_a}", match=[place_a],
        season="autumn", boundary=True)
    wish_b = Fact(
        key="wish_b", kind="wish", topic=f"{place_b}{act_b}",
        content=f"我想在冬天去{place_b}{act_b}", match=[place_b],
        season="winter")
    wish_c = Fact(
        key="wish_c", kind="wish", topic=f"{place_c}{act_c}",
        content=f"我想在冬天去{place_c}{act_c}", match=[place_c],
        season="winter")
    pref_keep_f = Fact(key="pref_keep", kind="preference", topic=pref_keep,
                       content=f"我很喜欢{pref_keep}", match=[pref_keep])
    pref_del_f = Fact(key="pref_del", kind="preference", topic=pref_del,
                      content=f"其实我不太吃得惯{pref_del}", match=[pref_del])
    story_f = Fact(key="story", kind="story", topic="共同故事",
                   content=f"还记得我们上次一起{story_text}吗？", match=[story_obj],
                   min_stage=1)
    commit_f = Fact(key="commit", kind="commitment", topic=commit_obj,
                    content=f"说好了，{commit_text}", match=[commit_obj])

    facts = {f.key: f for f in (wish_a, wish_b, wish_c, pref_keep_f,
                                pref_del_f, story_f, commit_f)}
    winter_target = wish_c if phenomena["update"] else wish_b
    if phenomena["update"]:
        wish_b.status = "superseded"
    if phenomena["fulfillment"]:
        commit_f.status = "fulfilled"

    turns: list[Turn] = []

    def say(month: int, text: str, **kw) -> None:
        turns.append(Turn(month=month, role="user", text=text, **kw))

    def filler(month: int, n: int) -> None:
        for t in rng.sample(FILLERS, n):
            say(month, t)

    # —— S1 · 春天（4 月）：相识、偏好、愿望 A + 边界 ——————————————
    if phenomena["multi-fact"]:
        say(4, f"你好呀，我叫{name}，{pref_keep_f.content}。", phenomena=["multi-fact"])
    else:
        say(4, f"你好呀，我叫{name}。")
        say(4, f"我跟你讲，{pref_keep_f.content}，每天都得来一份。")
    say(4, f"其实我不太吃得惯{pref_del}，之前尝过一次就不想再碰了。")
    say(4, f"{wish_a.content}，一直都想去了。")
    say(4, "不过到时候别催我订计划，我不想被追问这件事。")
    filler(4, rng.randint(1, 3))

    # —— S2 · 夏天（6 月）：承诺、共同故事、愿望 B ———————————————
    filler(6, rng.randint(1, 3))
    say(6, f"对了，{commit_f.content}，别忘了。")
    say(6, f"{story_f.content}每次想起来都觉得好笑。")
    say(6, f"还有啊，{wish_b.content}，这个不着急。")
    filler(6, rng.randint(1, 3))
    turns.append(Turn(month=6, role="user", text="", stage="building", phenomena=["stage"]))

    # —— S3 · 秋天（10 月）：边界、沉默或解除、被提起 ——————————————
    if phenomena["lift"]:
        say(10, f"之前{place_a}的事，现在可以聊了，你别有顾忌。", phenomena=["lift"])
        say(10, rng.choice(AMBIENT["autumn"]), recall=["wish_a"], phenomena=["lift"])
    else:
        say(10, rng.choice(AMBIENT["autumn"]),
            forbidden=["wish_a"], phenomena=["boundary"])   # 没提海，不许冒出来
    filler(10, rng.randint(1, 2))
    say(10, f"秋天到了，{place_a}的事我决定提上日程了！", recall=["wish_a"])
    filler(10, rng.randint(1, 2))

    # —— S4 · 深秋（11 月）：意愿变更 或 愿望重申 ————————————————
    if phenomena["update"]:
        say(11, f"我改主意了，我想在冬天去{place_c}{act_c}，不去{place_b}了。",
            phenomena=["update"])
    elif phenomena["restatement"]:
        say(11, f"我还是想在冬天去{place_b}{act_b}的，等雪季。", phenomena=["restatement"])
    filler(11, rng.randint(1, 3))

    # —— S5 · 冬天（12 月）：时节想起、兑现、删除与复活探针 ——————————
    turns.append(Turn(month=12, role="user", text="", stage="close", phenomena=["stage"]))
    if phenomena["fulfillment"]:
        # 兑现放 S5 开头：与 S6 的约定探针拉开距离，让「L2 承诺状态」
        # 成为唯一信号源（recent_turns 里的兑现句不算违规）
        say(12, f"上次答应你的{commit_obj}，这次给你带来了。", phenomena=["fulfillment"])
    winter_tag = ["update"] if phenomena["update"] else ["season"]
    say(12, rng.choice(AMBIENT["winter"]), recall=[winter_target.key],
        phenomena=winter_tag)                                # 时节探针：应该自然想起
    say(12, f"我是不是说过想去{winter_target.match[0]}来着？", recall=[winter_target.key])
    filler(12, rng.randint(0, 2))
    # 治理删除 1：删掉「不太吃得惯 X」那句原话（L0 消息删除）
    say(12, "最近肠胃不太好，在忌口。",
        deletions=[{"kind": "message", "ref": "pref_del"}], phenomena=["deletion"])
    turns[-1].rebuild = True                                 # 强制重建（补整理压力）
    say(12, f"我之前是不是说过自己不太吃得惯{pref_del}？",
        forbidden=["pref_del"], after_deletion=True, phenomena=["resurrection"])
    # 治理删除 2：删掉共同故事那句原话
    say(12, "想清理一下聊天记录。",
        deletions=[{"kind": "message", "ref": "story"}], phenomena=["deletion"])
    turns[-1].rebuild = True
    say(12, f"我们上次一起{story_text}是什么时候来着？",
        forbidden=["story"], after_deletion=True, phenomena=["resurrection"])

    # —— S6 · 冬末（1 月）：约定召回与收尾 ————————————————————
    if phenomena["fulfillment"]:
        say(1, "我们之间还有什么约定来着？",
            forbidden=["commit"], phenomena=["fulfillment"])  # 已兑现：不该再出现
    else:
        say(1, "我们之间还有什么约定来着？", recall=["commit"])
    filler(1, rng.randint(1, 3))

    conv_phenomena = [k for k, v in phenomena.items() if v]
    source = _fact_source_turns(turns, facts)
    _validate(turns, facts, source)
    return Conversation(cid=cid, facts=facts, turns=turns,
                        fact_source_turn=source, phenomena=conv_phenomena)


def _fact_source_turns(turns: list[Turn], facts: dict[str, Fact]) -> dict[str, int]:
    """每个事实的原话首次出现的轮次。"""
    source: dict[str, int] = {}
    for i, t in enumerate(turns):
        for key, f in facts.items():
            head = norm(f.content)[:8]
            if head and head in norm(t.text) and key not in source:
                source[key] = i
    return source


def _validate(turns: list[Turn], facts: dict[str, Fact],
              source: dict[str, int]) -> None:
    """语料自检：越界探针话术不得自带答案、探针不得早于原话、闲聊不得污染事实词。

    复活探针（after_deletion）刻意引用被删事实的词——用户提问本来就会
    这么问；判分时会排除当前输入本身，只看旧记忆有没有被翻出来。
    """
    for i, t in enumerate(turns):
        for key in t.forbidden:
            f = facts[key]
            if not t.after_deletion:
                for m in f.match:
                    assert norm(m) not in norm(t.text), f"探针泄漏：{key} / {t.text}"
            assert i > source.get(key, -1), f"被禁事实先于原话出现：{key}"
        for key in t.recall:
            assert i > source.get(key, -1), f"召回探针早于原话出现：{key}"
        # 探针轮之外，闲聊话术不得包含任何事实匹配词（防止污染）；
        # 现象轮（解除/变更/兑现/重申）会合法提到事实词，豁免
        if not (t.recall or t.forbidden) and not t.phenomena:
            for key, f in facts.items():
                if source.get(key) == i:
                    continue  # 原话轮本身当然包含匹配词
                for m in f.match:
                    assert norm(m) not in norm(t.text), \
                        f"闲聊污染事实词：{key} / {m} / {t.text}"


def generate_dataset(seed: int = 2026, n_conversations: int = 200) -> list[Conversation]:
    rng = random.Random(seed)
    return [generate_conversation(rng, f"conv-{i:04d}") for i in range(n_conversations)]


def dataset_fingerprint(convs: list[Conversation]) -> str:
    import hashlib
    payload = json.dumps([[asdict(t) for t in c.turns] for c in convs],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
