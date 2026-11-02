"""L2 · 关系档案：阶段、边界、承诺、没聊完的话题。

设计原则「关系是版本化的」的落地：

- 不用一个分数代表关系；状态是一个显式的字典，按 key 分片
  （``stage`` / ``boundaries`` / ``commitments`` / ``open_topics``）。
- 每次变更都是一条 ``relationship.op`` 事件，折叠函数 :func:`apply_op`
  是纯函数：同一份事件流永远折叠出同一份档案（重放确定性）。
- 每次变更在 ``relationship_log`` 里留底（before/after + 证据 seq），
  可以回滚到任意历史版本——回滚本身也是一次新增事件，不改旧账。
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .events import (
    OP_ADD_COMMITMENT, OP_CLOSE_TOPIC, OP_FULFILL_COMMITMENT, OP_LIFT_BOUNDARY,
    OP_OPEN_TOPIC, OP_RESTORE, OP_SET_BOUNDARY, OP_SET_STAGE,
)
from .tokens import tokenize

# 关系阶段（值越大越亲近）。阶段是慢变量，只能显式变更，不做自动迁移。
STAGES = ["acquaintance", "building", "close", "deep"]
STAGE_ZH = {
    "acquaintance": "初识",
    "building": "熟悉",
    "close": "亲近",
    "deep": "深厚",
}

KEY_STAGE = "stage"
KEY_BOUNDARIES = "boundaries"
KEY_COMMITMENTS = "commitments"
KEY_OPEN_TOPICS = "open_topics"
ALL_KEYS = (KEY_STAGE, KEY_BOUNDARIES, KEY_COMMITMENTS, KEY_OPEN_TOPICS)


def empty_state() -> dict[str, Any]:
    """空的档案快照。"""
    return {
        KEY_STAGE: {"value": STAGES[0]},
        KEY_BOUNDARIES: [],
        KEY_COMMITMENTS: [],
        KEY_OPEN_TOPICS: [],
    }


def stage_value(state: dict[str, Any]) -> int:
    """当前阶段的序号（0-3）。"""
    return STAGES.index(state[KEY_STAGE]["value"])


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return prefix + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def topic_tokens(topic: str) -> set[str]:
    """话题词袋（用于边界/输入的模糊匹配）。"""
    return set(tokenize(topic or ""))


def topics_overlap(a: str, b: str) -> bool:
    """两个话题的词袋是否有交集（删除匹配用，宁可宽松）。"""
    ta, tb = topic_tokens(a), topic_tokens(b)
    return bool(ta & tb) if (ta and tb) else (a or "") == (b or "")


def topics_related(a: str, b: str) -> bool:
    """两个话题是否足够相关（边界门用）。

    比任一 bigram 重叠更严：要求重叠数 ≥ 较小词袋的一半（至少 1）。
    这样「青岛看海」与「洱海看海」不会被彼此的边界误伤，
    而「看海」这样的泛指边界仍能命中「青岛看海」。
    """
    ta, tb = topic_tokens(a), topic_tokens(b)
    if not ta or not tb:
        return (a or "") == (b or "")
    overlap = len(ta & tb)
    return overlap >= max(1, min(len(ta), len(tb)) / 2)


def find_boundary(state: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
    """按 id 或话题找到边界。"""
    for b in state[KEY_BOUNDARIES]:
        if b["id"] == ref or b["topic"] == ref:
            return b
    return None


def find_open_topic(state: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
    for t in state[KEY_OPEN_TOPICS]:
        if t["id"] == ref or t["topic"] == ref:
            return t
    return None


def find_commitment(state: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
    for c in state[KEY_COMMITMENTS]:
        if c["id"] == ref or c["topic"] == ref or c["what"] == ref:
            return c
    return None


def apply_op(state: dict[str, Any], op: str, params: dict[str, Any],
             version: int, seq: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """把一条关系变更应用到状态上。

    返回 ``(新状态, 变更记录)``；``变更记录`` 含 key/op/before/after，
    由引擎写入 relationship_log 留底。纯函数：不改入参。
    """
    st = {
        KEY_STAGE: dict(state[KEY_STAGE]),
        KEY_BOUNDARIES: [dict(b) for b in state[KEY_BOUNDARIES]],
        KEY_COMMITMENTS: [dict(c) for c in state[KEY_COMMITMENTS]],
        KEY_OPEN_TOPICS: [dict(t) for t in state[KEY_OPEN_TOPICS]],
    }
    record: dict[str, Any] = {"op": op, "seq": seq}
    p = params

    if op == OP_SET_STAGE:
        value = p["value"]
        if value not in STAGES:
            raise ValueError(f"未知关系阶段：{value}")
        before = dict(st[KEY_STAGE])
        st[KEY_STAGE] = {"value": value, "since_seq": seq}
        record.update(key=KEY_STAGE, before=before, after=dict(st[KEY_STAGE]))

    elif op == OP_SET_BOUNDARY:
        topic = p["topic"].strip()
        existing = find_boundary(st, topic)
        before = dict(existing) if existing else None
        entry = existing or {
            "id": _stable_id("bd", topic, p.get("directive", "")),
            "topic": topic,
            "status": "active",
            "set_seq": seq,
        }
        entry.update(
            directive=p.get("directive", "不要主动提起"),
            scope=p.get("scope", "proactive"),  # proactive=只挡主动提及
            status="active",
            evidence=list(p.get("evidence", [])),
        )
        if not existing:
            st[KEY_BOUNDARIES].append(entry)
        record.update(key=KEY_BOUNDARIES, target=entry["id"], before=before,
                      after=dict(entry))

    elif op == OP_LIFT_BOUNDARY:
        b = find_boundary(st, p.get("ref", ""))
        if b is None:
            raise KeyError(f"找不到边界：{p.get('ref')}")
        before = dict(b)
        b["status"] = "lifted"
        b["lifted_seq"] = seq
        record.update(key=KEY_BOUNDARIES, target=b["id"], before=before, after=dict(b))

    elif op == OP_ADD_COMMITMENT:
        entry = {
            "id": _stable_id("cm", p.get("what", "")),
            "what": p["what"],
            "topic": p.get("topic", ""),
            "status": "open",
            "made_seq": seq,
            "evidence": list(p.get("evidence", [])),
        }
        st[KEY_COMMITMENTS].append(entry)
        record.update(key=KEY_COMMITMENTS, target=entry["id"], before=None, after=dict(entry))

    elif op == OP_FULFILL_COMMITMENT:
        c = find_commitment(st, p.get("ref", ""))
        if c is None:
            raise KeyError(f"找不到承诺：{p.get('ref')}")
        before = dict(c)
        c["status"] = "fulfilled"
        c["fulfilled_seq"] = seq
        record.update(key=KEY_COMMITMENTS, target=c["id"], before=before, after=dict(c))

    elif op == OP_OPEN_TOPIC:
        topic = p["topic"].strip()
        existing = find_open_topic(st, topic)
        before = dict(existing) if existing else None
        entry = existing or {
            "id": _stable_id("tp", topic),
            "topic": topic,
            "status": "open",
            "opened_seq": seq,
        }
        entry.update(
            status="open",
            note=p.get("note", ""),
            trigger=p.get("trigger") or {},
            evidence=list(p.get("evidence", [])),
        )
        if not existing:
            st[KEY_OPEN_TOPICS].append(entry)
        record.update(key=KEY_OPEN_TOPICS, target=entry["id"], before=before, after=dict(entry))

    elif op == OP_CLOSE_TOPIC:
        t = find_open_topic(st, p.get("ref", ""))
        if t is None:
            raise KeyError(f"找不到开放话题：{p.get('ref')}")
        before = dict(t)
        t["status"] = "closed"
        t["closed_seq"] = seq
        record.update(key=KEY_OPEN_TOPICS, target=t["id"], before=before, after=dict(t))

    elif op == OP_RESTORE:
        # 回滚：params.snapshot 是 {key: value} 的历史快照，一次恢复全部四个分片
        snapshot = {k: v for k, v in p.get("snapshot", {}).items() if k in ALL_KEYS}
        if not snapshot:
            raise ValueError("restore 快照为空")
        before = {k: st[k] for k in snapshot}
        for key, value in snapshot.items():
            st[key] = value
        record.update(key="*", before=before, after=snapshot,
                      note=f"回滚到版本 v{p.get('to_version', '?')}")

    else:
        raise ValueError(f"未知关系操作：{op}")

    return st, record
