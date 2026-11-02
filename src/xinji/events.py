"""L0 事件模型：账本里只追加的事件类型与哈希规则。

设计原则「原话不可变」的落地：L0 是一本可追溯的账，任何高层数据
（L1 记忆、L2 关系档案、墓碑、运行模式）都能通过重放这本账完整重建。

事件类型一览：

- ``message``          用户/助手的原话（唯一原件）
- ``relationship.op``  关系档案变更（阶段、边界、承诺、开放话题、回滚）
- ``memory.correction`` 纠正一条 L1 记忆（新事件，不改旧账）
- ``deletion``         删除（消息 / 记忆 / 话题），本质是墓碑事件
- ``system``           系统事件（一键停用、恢复、降级、备注）

每条事件带 ``hash`` 与 ``prev_hash``，形成哈希链：篡改任意一行都会让
``verify_chain()`` 失败——「一本可追溯的账」由此可被机器验证。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

# ---- 事件类型常量 -----------------------------------------------------------

MESSAGE = "message"
RELATIONSHIP_OP = "relationship.op"
MEMORY_CORRECTION = "memory.correction"
DELETION = "deletion"
SYSTEM = "system"

ALL_TYPES = (MESSAGE, RELATIONSHIP_OP, MEMORY_CORRECTION, DELETION, SYSTEM)

# relationship.op 支持的操作
OP_SET_STAGE = "set_stage"
OP_SET_BOUNDARY = "set_boundary"
OP_LIFT_BOUNDARY = "lift_boundary"
OP_ADD_COMMITMENT = "add_commitment"
OP_FULFILL_COMMITMENT = "fulfill_commitment"
OP_OPEN_TOPIC = "open_topic"
OP_CLOSE_TOPIC = "close_topic"
OP_RESTORE = "restore"  # 回滚到历史版本的快照

# deletion 支持的删除对象
DELETE_MESSAGE = "message"
DELETE_MEMORY = "memory"
DELETE_TOPIC = "topic"

# system 事件子类型
SYS_DISABLE = "disable"
SYS_ENABLE = "enable"
SYS_DEGRADE = "degrade"
SYS_RECOVER = "recover"
SYS_NOTE = "note"


def canonical_json(payload: dict[str, Any]) -> str:
    """载荷的规范序列化：排序键、紧凑分隔符、保留中文。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_hash(prev_hash: str, seq: int, type_: str, payload: dict[str, Any]) -> str:
    """计算事件哈希：sha256(prev_hash | seq | type | canonical_payload)。"""
    raw = f"{prev_hash}|{seq}|{type_}|{canonical_json(payload)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    """生成事件 / 派生对象使用的随机 id。"""
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class Event:
    """账本中的一条不可变事件。"""

    seq: int
    id: str
    ts: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str

    def summary(self) -> str:
        """审计视图里的一行摘要。"""
        p = self.payload
        if self.type == MESSAGE:
            text = str(p.get("text", "")).replace("\n", " ")
            if len(text) > 40:
                text = text[:40] + "…"
            return f"[{p.get('role', '?')}] {text}"
        if self.type == RELATIONSHIP_OP:
            return f"关系变更 {p.get('op')} {p.get('params', {})}"
        if self.type == MEMORY_CORRECTION:
            return f"纠正记忆 {p.get('memory_id')}"
        if self.type == DELETION:
            return f"删除 {p.get('kind')} × {len(p.get('targets', []))}（{p.get('reason', '')}）"
        if self.type == SYSTEM:
            return f"系统 {p.get('kind')}（{p.get('reason', '')}）"
        return canonical_json(p)[:60]


@dataclass
class EventBuilder:
    """组装新事件（尚未落账）。"""

    type: str
    payload: dict[str, Any]
    ts: str = field(default_factory=now_iso)
    id: str = field(default_factory=new_id)

    def build(self, seq: int, prev_hash: str) -> Event:
        h = event_hash(prev_hash, seq, self.type, self.payload)
        return Event(seq=seq, id=self.id, ts=self.ts, type=self.type,
                     payload=self.payload, prev_hash=prev_hash, hash=h)


def row_to_event(row) -> Event:
    """SQLite 行 → Event。"""
    return Event(
        seq=row["seq"],
        id=row["id"],
        ts=row["ts"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        prev_hash=row["prev_hash"],
        hash=row["hash"],
    )


def iter_event_types(payload_types: tuple[str, ...]) -> Iterator[str]:
    yield from payload_types


def first_or_none(items) -> Optional[Any]:
    return items[0] if items else None
