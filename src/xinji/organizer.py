"""L1 · 整理后的记忆：从 L0 原件派生摘要、情景、事实、画像与共同故事。

V1 使用**规则式整理器**（零依赖、确定性强、可离线运行），接口保持
「整理器 = 消息 → (记忆, 关系变更提案)」的纯函数形状，后续可替换为
LLM 整理器而不动引擎与账本。

关键约束：

- **确定性**：记忆 id / content_hash 由内容决定，重放同一份账本必然
  得到同一批记忆（这是「删除可验证」的前提）。
- **墓碑感知**：被删除的消息、记忆（按 id / 内容哈希 / 溯源集合）与
  话题，补整理时永远跳过——「删除就是删除，不能因为补整理又回来」。
- **边界即关系**：用户说出「别催我 / 别问了」时，整理器把它提取为
  ``set_boundary`` 关系变更提案（带原话证据），写入 L2 而不是埋在
  L1 文本里——边界必须在检索排序**之前**生效。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .events import (
    OP_ADD_COMMITMENT, OP_CLOSE_TOPIC, OP_FULFILL_COMMITMENT, OP_LIFT_BOUNDARY,
    OP_OPEN_TOPIC, OP_SET_BOUNDARY, Event,
)
from .tokens import estimate_tokens, tokenize

# ---- 模式库（V1 整理器认识的话语类别） ----------------------------------------

_SEASON_WORDS = {"春天": "spring", "夏天": "summer", "秋天": "autumn", "冬天": "winter",
                 "春季": "spring", "夏季": "summer", "秋季": "autumn", "冬季": "winter"}

# 边界：「别催我订计划」「不想被追问」「别提这件事」……
_BOUNDARY_RE = re.compile(
    r"(?P<head>别|不要|请别|不想|不愿意|别再)"
    r"(?P<verb>再)?(?P<act>催|追问|问|提|提起|聊|说|打扰|烦)"
    r"(?P<me>我)?(?P<prep>关于|有关)?"
    r"(?P<obj>[^，。！？!?,.\s]{0,12})"
)
_PLAN_GENERIC = {"计划", "安排", "进度", "这件事", "那件事", "这事", "那个"}  # 泛指词：需挂到具体话题

# 意愿/打算：「我想在秋天去看海」「打算明年去日本」……
_INTENT_RE = re.compile(
    r"我(?:想|想要|打算|准备|计划)(?:在|于)?"
    r"(?P<when>春天|夏天|秋天|冬天|春季|夏季|秋季|冬季|明年|今年|放假|寒假|暑假)?"
    r"(?P<rest>[^，。！？!?,\s]{1,20})"
)

# 偏好：「我喜欢吃辣」「我怕狗」……
_PREF_RE = re.compile(r"我(?:超|很|特别)?(?:喜欢|爱|讨厌|不喜欢|怕)(?P<obj>[^，。！？!?,\s]{1,12})")

# 画像：「我叫小满」「我的名字是……」
_NAME_RE = re.compile(r"我(?:的名字是|叫)(?P<name>[\u4e00-\u9fff]{1,6})(?=[，。！？,!\s]|$)")

# 共同故事：「还记得我们……」「我们上次……」
_STORY_RE = re.compile(r"(还记得|想起来了?|我们)(?:上次|那天|那年|曾经|一起|之前)?")

# 承诺（多为用户方）：「我保证……」「说好了……」「我们约好……」
_COMMIT_RE = re.compile(r"(我保证|我答应你|我承诺|说好了|我们约好了?|约好了?)")

# 意愿变更：「我改主意了……不去X了」→ 关闭旧话题
_UPDATE_CLOSE_RE = re.compile(r"(?:不想|不准备|不打算|决定不去|不去)(?:在|去|搞|弄)?"
                              r"(?P<obj>[^，。！？!?,\s]{2,10}?)(?:了|这个|的计划|的打算)")

# 边界解除：「之前X的事，现在可以聊了」「你可以问了」
_LIFT_RE = re.compile(r"(?:现在)?(?:可以聊|可以提|可以问|能聊|能提|能问|别顾忌|不用顾忌|别有顾忌)")

# 承诺兑现：「答应你的X带来了」「上次的事办妥了」
_FULFILL_RE = re.compile(r"(?:上次)?(?:答应|承诺)(?:你的|给你)?(?P<obj>[\u4e00-\u9fff]{1,10})"
                         r"|(?:兑现了|办妥了|做到了)")

_SENT_SPLIT = re.compile(r"[。！？；!?;\n]+")

_KIND_FACT = "fact"
_KIND_EPISODE = "episode"
_KIND_STORY = "story"
_KIND_PROFILE = "profile"
_KIND_SUMMARY = "summary"

DIRECTIVE_ZH = {
    "催": "别催：不要主动询问进度，也不要催她做安排",
    "追问": "别追问：她想说的时候自然会说",
    "问": "别问：不要主动问起这个话题",
    "提": "别提：不要主动提起这个话题",
    "提起": "别提：不要主动提起这个话题",
    "聊": "别聊：不要主动聊起这个话题",
    "说": "别说：不要主动说起这个话题",
    "打扰": "别打扰：给她安静的空间",
    "烦": "别烦：不要反复提及",
}


def _norm(text: str) -> str:
    """内容归一化：去掉空白，用于内容哈希。"""
    return re.sub(r"\s+", "", text)


def content_hash(content: str) -> str:
    return hashlib.sha256(_norm(content).encode("utf-8")).hexdigest()


def memory_id(kind: str, content: str) -> str:
    raw = f"{kind}|{_norm(content)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _clean_topic(obj: str) -> str:
    """话题清洗：去掉常见动词前缀与泛指尾巴。"""
    t = re.sub(r"^(去|做|弄|订|定|安排|关于|有关|喝|吃|买|读|听|养)", "", obj.strip())
    t = re.sub(r"(了|的|一下|这事|这事儿)$", "", t)
    return t.strip()


@dataclass
class Extraction:
    """一条消息的整理结果：派生记忆 + 关系变更提案。"""

    memories: list[dict[str, Any]] = field(default_factory=list)
    ops: list[dict[str, Any]] = field(default_factory=list)


def extract(message: Event, open_topics: Iterable[dict[str, Any]] = (),
            boundaries: Iterable[dict[str, Any]] = (),
            commitments: Iterable[dict[str, Any]] = ()) -> Extraction:
    """从一条用户消息提取 L1 记忆与 L2 关系变更提案（纯函数）。

    ``open_topics`` / ``boundaries`` / ``commitments`` 是说话时刻的
    关系档案，用于：把「别催我订计划」这类泛指边界挂到具体话题上、
    识别「现在可以聊了」（解除边界）、「不去X了」（意愿变更）、
    「答应你的X带来了」（承诺兑现）等需要对照现状的话语。
    """
    if message.payload.get("role") != "user":
        return Extraction()

    text = str(message.payload.get("text", ""))
    seq = message.seq
    result = Extraction()
    topics_now = [t for t in open_topics if t.get("status") == "open"]
    bounds_now = [b for b in boundaries if b.get("status") == "active"]
    commits_now = [c for c in commitments if c.get("status") == "open"]

    for sent in _SENT_SPLIT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        _extract_update_close(result, sent, seq, topics_now)
        _extract_lift(result, sent, seq, bounds_now)
        _extract_fulfill(result, sent, seq, commits_now)
        _extract_boundary(result, sent, seq, topics_now)
        _extract_intent(result, sent, seq)
        _extract_preference(result, sent, seq)
        _extract_name(result, sent, seq)
        _extract_story(result, sent, seq)
        _extract_commitment(result, sent, seq)

    # 整句话作为情景记忆（episode）留档：RAG 式兜底，供跨会话检索。
    if text.strip():
        result.memories.append(_make_memory(
            kind=_KIND_EPISODE, topic="", content=text.strip(),
            provenance=[seq], confidence=0.6, min_stage=0,
        ))
    return result


def _extract_update_close(result: Extraction, sent: str, seq: int,
                          open_topics: list[dict[str, Any]]) -> None:
    """「我改主意了，不去X了」→ 关闭被放弃的开放话题。"""
    m = _UPDATE_CLOSE_RE.search(sent)
    if not m:
        return
    obj = _clean_topic(m.group("obj") or "")
    if not obj:
        return
    hit = next((t for t in open_topics
                if t["status"] == "open" and _topics_share(t["topic"], obj)), None)
    if hit is None:
        return
    result.ops.append({
        "op": OP_CLOSE_TOPIC,
        "params": {"ref": hit["topic"], "evidence": [seq],
                   "note": "用户表示放弃了这个计划"},
    })


def _extract_lift(result: Extraction, sent: str, seq: int,
                  boundaries: list[dict[str, Any]]) -> None:
    """「现在可以聊了」→ 解除边界（优先解除话里点名的，否则最近的）。"""
    if not _LIFT_RE.search(sent):
        return
    if not boundaries:
        return
    sent_tokens = set(tokenize(sent))
    named = [b for b in boundaries if set(tokenize(b["topic"])) & sent_tokens]
    target = named[0] if named else boundaries[-1]
    result.ops.append({
        "op": OP_LIFT_BOUNDARY,
        "params": {"ref": target["topic"], "evidence": [seq],
                   "note": "用户表示现在可以聊了"},
    })


def _extract_fulfill(result: Extraction, sent: str, seq: int,
                     commitments: list[dict[str, Any]]) -> None:
    """「答应你的X带来了」→ 兑现匹配的承诺。

    优先按句中点名的宾语找承诺（宾语须是承诺内容的子串）；
    没点名时退回词袋重叠最多的那条。
    """
    m = _FULFILL_RE.search(sent)
    if not m or not commitments:
        return
    best = None
    obj = m.group("obj")
    if obj:
        best = next((c for c in commitments if obj in c["what"]), None)
    if best is None:
        sent_tokens = set(tokenize(sent))
        best_score = 0
        for c in commitments:
            score = len(set(tokenize(c["what"])) & sent_tokens)
            if score > best_score:
                best, best_score = c, score
    if best is None:
        return
    result.ops.append({
        "op": OP_FULFILL_COMMITMENT,
        "params": {"ref": best["what"], "evidence": [seq],
                   "note": "用户表示已兑现"},
    })


def _topics_share(a: str, b: str) -> bool:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return bool(ta & tb) if (ta and tb) else a == b


# ---- 各类抽取 -----------------------------------------------------------------


def _extract_boundary(result: Extraction, sent: str, seq: int,
                      open_topics: list[dict[str, Any]]) -> None:
    m = _BOUNDARY_RE.search(sent)
    if not m:
        return
    obj = _clean_topic(m.group("obj") or "")
    act = m.group("act")
    if obj in _PLAN_GENERIC or not obj:
        # 泛指边界：挂到最近一个开放话题（比如刚许下的「看海」愿望）
        if open_topics:
            obj = open_topics[-1]["topic"]
        else:
            obj = obj or "这件事"
    result.ops.append({
        "op": OP_SET_BOUNDARY,
        "params": {
            "topic": obj,
            "directive": DIRECTIVE_ZH.get(act, "不要主动提起"),
            "scope": "proactive",
            "evidence": [seq],
        },
    })


def _extract_intent(result: Extraction, sent: str, seq: int) -> None:
    m = _INTENT_RE.search(sent)
    if not m:
        return
    rest = m.group("rest") or ""
    when = m.group("when") or ""
    topic = _clean_topic(rest) or rest
    content = f"想{'在' + when if when else ''}{rest}" if rest else sent
    result.memories.append(_make_memory(
        kind=_KIND_FACT, topic=topic, content=content,
        provenance=[seq], confidence=0.9, min_stage=0,
        fact_type="intention",
    ))
    trigger: dict[str, Any] = {}
    if when in _SEASON_WORDS:
        trigger = {"season": _SEASON_WORDS[when]}
    result.ops.append({
        "op": OP_OPEN_TOPIC,
        "params": {"topic": topic, "trigger": trigger, "note": content,
                   "evidence": [seq]},
    })


def _extract_preference(result: Extraction, sent: str, seq: int) -> None:
    m = _PREF_RE.search(sent)
    if not m:
        return
    obj = _clean_topic(m.group("obj") or "")
    if not obj:
        return
    result.memories.append(_make_memory(
        kind=_KIND_FACT, topic=obj, content=m.group(0),
        provenance=[seq], confidence=0.85, min_stage=0,
        fact_type="preference",
    ))


def _extract_name(result: Extraction, sent: str, seq: int) -> None:
    m = _NAME_RE.search(sent)
    if not m:
        return
    name = m.group("name")
    result.memories.append(_make_memory(
        kind=_KIND_PROFILE, topic="名字", content=f"她叫{name}",
        provenance=[seq], confidence=0.9, min_stage=0,
    ))


def _extract_story(result: Extraction, sent: str, seq: int) -> None:
    m = _STORY_RE.search(sent)
    if not m:
        return
    result.memories.append(_make_memory(
        kind=_KIND_STORY, topic="共同故事", content=sent,
        provenance=[seq], confidence=0.7, min_stage=1,  # 共同故事需要关系到「熟悉」以上
    ))


def _extract_commitment(result: Extraction, sent: str, seq: int) -> None:
    m = _COMMIT_RE.search(sent)
    if not m:
        return
    what = sent[m.end():].strip() or sent
    result.ops.append({
        "op": OP_ADD_COMMITMENT,
        "params": {"what": what, "topic": _clean_topic(what[:8]), "evidence": [seq]},
    })


def _make_memory(*, kind: str, topic: str, content: str, provenance: list[int],
                 confidence: float, min_stage: int,
                 fact_type: Optional[str] = None) -> dict[str, Any]:
    mem = {
        "id": memory_id(kind, content),
        "kind": kind,
        "topic": topic,
        "content": content,
        "content_hash": content_hash(content),
        "provenance": list(provenance),
        "min_stage": min_stage,
        "confidence": confidence,
        "tokens": estimate_tokens(content),
        "derived_at": max(provenance),
        "corrected": False,
    }
    if fact_type:
        mem["fact_type"] = fact_type
    return mem


# ---- 墓碑过滤 -----------------------------------------------------------------


def tombstone_filters(tombstones: list[dict[str, Any]]) -> dict[str, Any]:
    """把墓碑表整理成快速过滤结构。

    记忆墓碑按 id 与内容哈希匹配：整理器是确定性的，同一条原话永远
    派生出同一个 id / 哈希，因此这两项已足以阻止复活；不再按溯源
    集合匹配，避免「删一条记忆误伤同一句话派生的其它记忆」。
    """
    return {
        "messages": {int(t["target"]) for t in tombstones if t["kind"] == "message"},
        "memory_ids": {t["target"] for t in tombstones if t["kind"] == "memory"},
        "content_hashes": {t["content_hash"] for t in tombstones
                           if t["kind"] == "memory" and t.get("content_hash")},
        "topics": {t["target"] for t in tombstones if t["kind"] == "topic"},
    }


def is_tombstoned(memory: dict[str, Any], filters: dict[str, Any]) -> bool:
    """一条派生记忆是否已被删除覆盖（id / 内容哈希 / 话题）。"""
    if memory["id"] in filters["memory_ids"]:
        return True
    if memory["content_hash"] in filters["content_hashes"]:
        return True
    topic = memory.get("topic", "")
    if topic and any(_topics_match(topic, t) for t in filters["topics"]):
        return True
    return False


def _topics_match(a: str, b: str) -> bool:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    return bool(ta & tb) if (ta and tb) else a == b


# ---- 会话摘要 -----------------------------------------------------------------

SUMMARY_EVERY = 8  # 每整理 8 条消息生成一份片段摘要


def build_summary(messages: list[Event], topics: list[str]) -> dict[str, Any]:
    """给一段消息生成轻量摘要（确定性的：话题列表 + 首句）。"""
    seqs = [m.seq for m in messages]
    first_user = next((str(m.payload.get("text", "")) for m in messages
                       if m.payload.get("role") == "user"), "")
    first_user = first_user[:50].strip()
    topic_str = "、".join(dict.fromkeys(topics)) if topics else "日常闲聊"
    content = f"会话片段（第 {seqs[0]}–{seqs[-1]} 句）：主要话题：{topic_str}。由「{first_user}」开始。"
    return _make_memory(
        kind=_KIND_SUMMARY, topic="", content=content,
        provenance=seqs, confidence=0.8, min_stage=0,
    )
