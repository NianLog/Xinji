"""L3 · 开口前的判断：该不该提、提哪几条。同步读路径上的最后一道门。

这是心记区别于 RAG 的核心：**边界、关系阶段与承诺优先于相似度。**

判断顺序（先硬后软）：

1. **阶段门**：共同故事等记忆标注了最低关系阶段，阶段不够就不提。
2. **证据门（默认沉默）**：置信度不足的记忆不提；检索零分 = 无证据。
3. **边界门**：话题命中有效边界时——
   - 当前输入自己聊到了这个话题（用户主动提起）→ 允许，但附上
     「分寸提示」（可以自然接上，别追问、别催）；
   - 否则，主动提及被挡下（`boundary-proactive`）。
   记得，但不把旧愿望变成压力。
4. **新鲜度门**：最近几轮刚提过的记忆不再重复（不唠叨）。

通过全部门禁的候选按证据强度排序，挑 3–5 条装进约 8k token 预算；
一条都不该提时，返回空包 + 「默认沉默」——说不，也是正确答案。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .relationship import KEY_BOUNDARIES, KEY_OPEN_TOPICS, STAGES, STAGE_ZH, topics_related
from .tokens import tokenize

MAX_ITEMS = 5
MIN_ITEMS = 3          # 「挑 3–5 条」：够 3 条挑 3 条，不够就有多少挑多少
TOKEN_BUDGET = 8000
MIN_CONFIDENCE = 0.35
RECENT_MENTION_WINDOW = 3   # 最近 3 轮内提过的记忆不再主动重复


def judge(candidate: dict[str, Any], *, source: str, score: float,
          input_tokens: set[str], state: dict[str, Any], current_turn: int
          ) -> dict[str, Any]:
    """对单个候选记忆做 L3 判断，返回裁决（allow / block + 原因）。"""
    topic = candidate.get("topic", "")

    # 1) 阶段门：关系还没到，就不提
    from .relationship import stage_value
    if candidate.get("min_stage", 0) > stage_value(state):
        needed = STAGES[candidate["min_stage"]]
        return {"decision": "block", "reason": "stage-below",
                "detail": f"该记忆至少需要「{STAGE_ZH[needed]}」阶段，"
                          f"当前是「{STAGE_ZH[state['stage']['value']]}」"}

    # 2) 证据门：置信度不足 → 默认沉默
    if candidate.get("confidence", 1.0) < MIN_CONFIDENCE:
        return {"decision": "block", "reason": "low-confidence",
                "detail": f"证据不足（置信度 {candidate.get('confidence', 0):.2f} < {MIN_CONFIDENCE}）"}

    # 3) 边界门：边界先于相似度
    #    不仅查记忆的话题标签，也查记忆内容——情景记忆（topic 为空）
    #    照样可能带着被边界覆盖的事，不能因为「没有话题」就漏过；
    #    也不区分来源——被相似度召回同样是一种「主动」：用户没提起
    #    这个话题时，无论记忆是被时节扫描还是被检索命中，都不出口。
    input_touches = bool(input_tokens & set(tokenize(topic))) if topic else False
    directive: Optional[dict[str, Any]] = None
    for b in state[KEY_BOUNDARIES]:
        if b.get("status") != "active":
            continue
        if not (topics_related(b["topic"], topic)
                or _topic_in_text(b["topic"], candidate["content"])):
            continue
        directive = b
        user_initiated = input_touches or bool(input_tokens & set(tokenize(b["topic"])))
        if not user_initiated:
            return {"decision": "block", "reason": "boundary-proactive",
                    "detail": f"边界「{b['directive']}」仍有效：她没提起，就先安静",
                    "boundary": {"topic": b["topic"], "directive": b["directive"]}}
        if b.get("scope", "proactive") == "any":
            return {"decision": "block", "reason": "boundary-any",
                    "detail": f"边界「{b['directive']}」为完全静默：任何提及都被挡下",
                    "boundary": {"topic": b["topic"], "directive": b["directive"]}}
        # 用户主动提起：允许接上，但带上分寸提示
        break

    # 4) 新鲜度门：刚提过的不重复
    last = candidate.get("mention_last_turn")
    if last is not None and current_turn is not None and 0 < current_turn - last <= RECENT_MENTION_WINDOW:
        return {"decision": "block", "reason": "recently-mentioned",
                "detail": f"第 {last} 轮刚提过，不唠叨"}

    verdict: dict[str, Any] = {"decision": "allow", "reason": "allowed"}
    if directive is not None:
        verdict["reason"] = "allowed-user-initiated" if input_touches else "allowed"
        verdict["directive"] = {"topic": directive["topic"],
                                "text": directive["directive"]}
    return verdict


_PUNCT_RE = re.compile(r"[\s，。！？!?,.、：；;：'\"“”『』（）()\[\]·…—\-]")


def _topic_in_text(boundary_topic: str, text: str) -> bool:
    """边界话题是否作为完整词串出现在记忆内容里（去空白标点后的
    子串匹配，泛指话题如「看海」也能命中「…去青岛看海…」）。"""
    nb = _PUNCT_RE.sub("", boundary_topic)
    return bool(nb) and nb in _PUNCT_RE.sub("", text)


def select_and_pack(allowed: list[dict[str, Any]], budget: int = TOKEN_BUDGET
                    ) -> list[dict[str, Any]]:
    """把通过门禁的候选挑 3–5 条装进预算（按证据强度降序）。

    同一话题的多条记忆（比如同一愿望的多次复述）只保留证据最强的
    一条——重复内容只会浪费预算，不会让模型「记得更牢」。
    """
    picked: list[dict[str, Any]] = []
    used = 0
    seen_topics: set[str] = set()
    for item in sorted(allowed, key=lambda x: x["score"], reverse=True):
        if len(picked) >= MAX_ITEMS:
            break
        topic = item["memory"].get("topic", "")
        if topic and topic in seen_topics:
            continue
        tokens = item["memory"].get("tokens", 0)
        if used + tokens > budget:
            continue
        picked.append(item)
        used += tokens
        if topic:
            seen_topics.add(topic)
        if len(picked) >= MIN_ITEMS and used >= budget * 0.7:
            break
    return picked


def build_guidance(picked: list[dict[str, Any]], suppressed: list[dict[str, Any]],
                   state: dict[str, Any]) -> str:
    """给宿主模型的一句「分寸」提示：能提什么、怎么提、什么别碰。"""
    directives = []
    for item in picked:
        d = item.get("verdict", {}).get("directive")
        if d and d not in directives:
            directives.append(d)
    for s in suppressed:
        if s.get("reason") == "boundary-proactive":
            b = s.get("boundary")
            if b:
                directives.append({"topic": b["topic"], "text": b["directive"]})

    parts: list[str] = []
    if picked:
        topics = "、".join(dict.fromkeys(
            i["memory"].get("topic") or i["memory"]["kind"] for i in picked if i["memory"].get("topic")))
        parts.append(f"可以自然接上：{topics}" if topics else "可以引用以上记忆")
    else:
        parts.append("本轮不主动提及任何旧记忆（默认沉默）")
    if directives:
        parts.append("分寸：" + "；".join(f"「{d['text']}」（{d['topic']}）" for d in directives))
    return "。".join(parts) + "。"
