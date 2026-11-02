"""SQLite 存储层：L0 追加式账本 + 可重建的派生表。

账本表（``ledger``）只提供 INSERT，没有任何 UPDATE / DELETE 接口——
「原话不可变」由 API 形状保证，哈希链提供篡改证据。

派生表（记忆、关系档案、墓碑、元信息）随时可以清空后从账本重放重建，
这是「删除可验证：重放历史，已删除内容不得复活」的实现基础。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .events import Event, EventBuilder, row_to_event

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq       INTEGER PRIMARY KEY,
    id        TEXT NOT NULL UNIQUE,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,
    payload   TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);

-- L1 整理后的记忆（可全部从 ledger 重建）
CREATE TABLE IF NOT EXISTS memories (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,          -- fact | episode | story | profile | summary
    topic        TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    provenance   TEXT NOT NULL,          -- JSON: 来源消息 seq 列表
    min_stage    INTEGER NOT NULL DEFAULT 0,
    confidence   REAL NOT NULL DEFAULT 1.0,
    tokens       INTEGER NOT NULL DEFAULT 0,
    derived_at   INTEGER NOT NULL,       -- 来源最大 seq（重放确定性）
    corrected    INTEGER NOT NULL DEFAULT 0,
    fact_type    TEXT NOT NULL DEFAULT '',  -- intention | preference | …（fact 细分）
    mention_last_turn INTEGER            -- 运行时缓存，不参与重放比对
);

-- L2 关系档案：当前有效版本（可从 ledger 重建）
CREATE TABLE IF NOT EXISTS relationship (
    key     TEXT PRIMARY KEY,            -- stage | boundaries | commitments | open_topics
    value   TEXT NOT NULL,               -- JSON
    version INTEGER NOT NULL
);

-- L2 变更留底：每次改动都有 before/after 与证据
CREATE TABLE IF NOT EXISTS relationship_log (
    version INTEGER PRIMARY KEY,
    seq     INTEGER NOT NULL,
    op      TEXT NOT NULL,
    target  TEXT NOT NULL DEFAULT '',
    before  TEXT,
    after   TEXT
);

-- 墓碑：删除的登记处，重放时按同样的顺序重建
CREATE TABLE IF NOT EXISTS tombstones (
    rowid_tomb  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,           -- message | memory | topic
    target      TEXT NOT NULL,           -- 消息 seq / 记忆 id / 话题
    content_hash TEXT NOT NULL DEFAULT '',
    provenance  TEXT NOT NULL DEFAULT '[]',
    reason      TEXT NOT NULL DEFAULT '',
    seq         INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger(type);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_tombstones_kind ON tombstones(kind, target);
"""

# 重放时需要清空重建的派生表（账本永远不动）
DERIVED_TABLES = ("memories", "relationship", "relationship_log", "tombstones")

# 派生元信息键（重放时重建）；其余 meta 键视为运行时状态
DERIVED_META_KEYS = ("organized_upto", "turn_count", "mode", "relationship_version")


class LedgerIntegrityError(Exception):
    """账本哈希链校验失败（可能被外部篡改）。"""


class Store:
    """单个心记库（一个 SQLite 文件）的读写门面。"""

    def __init__(self, path: str):
        self.path = path
        # check_same_thread=False：HTTP 服务等多线程宿主跨线程复用连接；
        # 调用方须自行串行化（engine 侧由锁保证）。
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ---- 事务 ---------------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """写事务：BEGIN IMMEDIATE 串行化写入，异常时回滚。"""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    # ---- L0 账本：只追加 -----------------------------------------------------

    def append(self, builder: EventBuilder) -> Event:
        """把一条事件追加到账本末尾（原子，带哈希链）。"""
        with self.tx() as conn:
            row = conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else "0" * 64
            next_seq = (row["seq"] + 1) if row else 1
            event = builder.build(next_seq, prev_hash)
            conn.execute(
                "INSERT INTO ledger(seq, id, ts, type, payload, prev_hash, hash) VALUES(?,?,?,?,?,?,?)",
                (event.seq, event.id, event.ts, event.type,
                 json.dumps(event.payload, ensure_ascii=False),
                 event.prev_hash, event.hash),
            )
        return event

    def append_many(self, builders: list[EventBuilder]) -> list[Event]:
        """按顺序原子追加多条事件。"""
        events: list[Event] = []
        with self.tx() as conn:
            row = conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else "0" * 64
            next_seq = (row["seq"] + 1) if row else 1
            for b in builders:
                event = b.build(next_seq, prev_hash)
                conn.execute(
                    "INSERT INTO ledger(seq, id, ts, type, payload, prev_hash, hash) VALUES(?,?,?,?,?,?,?)",
                    (event.seq, event.id, event.ts, event.type,
                     json.dumps(event.payload, ensure_ascii=False),
                     event.prev_hash, event.hash),
                )
                events.append(event)
                prev_hash = event.hash
                next_seq += 1
        return events

    def head(self) -> Optional[tuple[int, str]]:
        """账本末尾的 (seq, hash)。"""
        row = self.conn.execute("SELECT seq, hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return (row["seq"], row["hash"]) if row else None

    def events(self, seq_from: int = 0, type_: Optional[str] = None) -> Iterator[Event]:
        """按 seq 升序迭代事件。"""
        sql = "SELECT * FROM ledger WHERE seq > ?"
        params: list[Any] = [seq_from]
        if type_:
            sql += " AND type = ?"
            params.append(type_)
        sql += " ORDER BY seq ASC"
        for row in self.conn.execute(sql, params):
            yield row_to_event(row)

    def event(self, seq: int) -> Optional[Event]:
        row = self.conn.execute("SELECT * FROM ledger WHERE seq = ?", (seq,)).fetchone()
        return row_to_event(row) if row else None

    def verify_chain(self) -> list[int]:
        """校验整条哈希链，返回损坏位置列表（空列表 = 完好）。"""
        prev_hash = "0" * 64
        expect_seq = 1
        broken: list[int] = []
        for ev in self.events():
            if ev.seq != expect_seq or ev.prev_hash != prev_hash or ev.hash != self._rehash(ev):
                broken.append(ev.seq)
            prev_hash = ev.hash
            expect_seq += 1
        return broken

    @staticmethod
    def _rehash(ev: Event) -> str:
        from .events import event_hash
        return event_hash(ev.prev_hash, ev.seq, ev.type, ev.payload)

    # ---- L1 记忆 ------------------------------------------------------------

    def upsert_memory(self, m: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO memories(id, kind, topic, content, content_hash, provenance,
                                        min_stage, confidence, tokens, derived_at, corrected, fact_type)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     content=excluded.content, content_hash=excluded.content_hash,
                     tokens=excluded.tokens, corrected=excluded.corrected""",
                (m["id"], m["kind"], m.get("topic", ""), m["content"], m["content_hash"],
                 json.dumps(m.get("provenance", []), ensure_ascii=False),
                 int(m.get("min_stage", 0)), float(m.get("confidence", 1.0)),
                 int(m.get("tokens", 0)), int(m.get("derived_at", 0)),
                 int(m.get("corrected", 0)), m.get("fact_type", "")),
            )

    def memories(self, kind: Optional[str] = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY derived_at ASC, id ASC"
        return [self._memory_row(r) for r in self.conn.execute(sql, params)]

    def memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_row(row) if row else None

    @staticmethod
    def _memory_row(r) -> dict[str, Any]:
        return {
            "id": r["id"], "kind": r["kind"], "topic": r["topic"], "content": r["content"],
            "content_hash": r["content_hash"], "provenance": json.loads(r["provenance"]),
            "min_stage": r["min_stage"], "confidence": r["confidence"], "tokens": r["tokens"],
            "derived_at": r["derived_at"], "corrected": bool(r["corrected"]),
            "fact_type": r["fact_type"], "mention_last_turn": r["mention_last_turn"],
        }

    def update_memory_content(self, memory_id: str, content: str, content_hash: str,
                              tokens: int) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE memories SET content=?, content_hash=?, tokens=?, corrected=1 WHERE id=?",
                (content, content_hash, tokens, memory_id),
            )

    def drop_memories(self, ids: list[str]) -> int:
        """删除派生记忆行（L0 不动；墓碑已在删除事件里登记）。"""
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        with self.tx() as conn:
            cur = conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", ids)
        return cur.rowcount

    def set_mention_turn(self, memory_ids: list[str], turn: int) -> None:
        if not memory_ids:
            return
        marks = ",".join("?" * len(memory_ids))
        with self.tx() as conn:
            conn.execute(f"UPDATE memories SET mention_last_turn=? WHERE id IN ({marks})",
                         [turn, *memory_ids])

    # ---- L2 关系档案 ----------------------------------------------------------

    def relationship_snapshot(self) -> dict[str, Any]:
        rows = self.conn.execute("SELECT key, value, version FROM relationship").fetchall()
        return {r["key"]: {"value": json.loads(r["value"]), "version": r["version"]} for r in rows}

    def put_relationship(self, key: str, value: Any, version: int) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO relationship(key, value, version) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, version=excluded.version""",
                (key, json.dumps(value, ensure_ascii=False), version),
            )

    def log_relationship(self, version: int, seq: int, op: str, target: str,
                         before: Any, after: Any) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO relationship_log(version, seq, op, target, before, after)"
                " VALUES(?,?,?,?,?,?)",
                (version, seq, op, target,
                 json.dumps(before, ensure_ascii=False) if before is not None else None,
                 json.dumps(after, ensure_ascii=False) if after is not None else None),
            )

    def relationship_log(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT version, seq, op, target, before, after FROM relationship_log ORDER BY version"
        ).fetchall()
        return [{
            "version": r["version"], "seq": r["seq"], "op": r["op"], "target": r["target"],
            "before": json.loads(r["before"]) if r["before"] else None,
            "after": json.loads(r["after"]) if r["after"] else None,
        } for r in rows]

    # ---- 墓碑 ----------------------------------------------------------------

    def add_tombstones(self, rows: list[dict[str, Any]]) -> None:
        with self.tx() as conn:
            for t in rows:
                conn.execute(
                    "INSERT INTO tombstones(kind, target, content_hash, provenance, reason, seq)"
                    " VALUES(?,?,?,?,?,?)",
                    (t["kind"], t["target"], t.get("content_hash", ""),
                     json.dumps(t.get("provenance", []), ensure_ascii=False),
                     t.get("reason", ""), t["seq"]),
                )

    def tombstones(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT kind, target, content_hash, provenance, reason, seq FROM tombstones"
            " ORDER BY rowid_tomb"
        ).fetchall()
        return [{
            "kind": r["kind"], "target": r["target"], "content_hash": r["content_hash"],
            "provenance": json.loads(r["provenance"]), "reason": r["reason"], "seq": r["seq"],
        } for r in rows]

    # ---- 元信息 ----------------------------------------------------------------

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- 重放 -----------------------------------------------------------------

    def clear_derived(self) -> None:
        """清空全部派生表与派生元信息（账本原封不动）。"""
        with self.tx() as conn:
            for table in DERIVED_TABLES:
                conn.execute(f"DELETE FROM {table}")
            for key in DERIVED_META_KEYS:
                conn.execute("DELETE FROM meta WHERE key=?", (key,))

    def copy_ledger_into(self, other: "Store") -> None:
        """把本库账本逐字复制到另一个库（重放的第一步）。"""
        other.clear_ledger()
        rows = self.conn.execute(
            "SELECT seq, id, ts, type, payload, prev_hash, hash FROM ledger ORDER BY seq"
        ).fetchall()
        with other.tx() as conn:
            conn.executemany(
                "INSERT INTO ledger(seq, id, ts, type, payload, prev_hash, hash) VALUES(?,?,?,?,?,?,?)",
                [(r["seq"], r["id"], r["ts"], r["type"], r["payload"], r["prev_hash"], r["hash"])
                 for r in rows],
            )

    def clear_ledger(self) -> None:
        """清空账本（仅用于重放目标库初始化，绝不用于运行中的库）。"""
        with self.tx() as conn:
            conn.execute("DELETE FROM ledger")
