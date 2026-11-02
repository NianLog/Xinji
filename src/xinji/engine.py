"""心记引擎：把五层记忆组织成「写 / 读 / 错」三条路径与一整套治理操作。

写路径（先记下来）：

    hear() → 原话立刻进 L0 账本并返回确认；整理（organize）随后进行，
    不阻塞聊天。

读路径（想好再说）：

    read() → 检索相关记忆 → L3 判断（边界先于相似度、默认沉默）→
    挑 3–5 条装进约 8k token 预算，附「分寸提示」与被压下的候选及原因。

错路径（先降级，不装记得）：

    一键停用或任何读/整理故障 → 自动降级：关掉可选旧事，只带当前输入、
    最近几轮、有效画像摘要与当前硬边界，聊天照常；恢复后补整理。

治理（L4）：查看、纠正、删除、导出、重放、审计、一键停用，全部以
「追加事件」的方式落在 L0 上——原话不可变，删除也只是墓碑事件，
因此重放永远能验证「已删除的内容没有复活」。
"""

from __future__ import annotations

import json
import tempfile
import os
from datetime import datetime
from typing import Any, Callable, Optional

from . import judgment, organizer, retrieval
from .events import (
    DELETION, MEMORY_CORRECTION, MESSAGE, RELATIONSHIP_OP, SYSTEM,
    Event, EventBuilder, now_iso,
    OP_ADD_COMMITMENT, OP_CLOSE_TOPIC, OP_FULFILL_COMMITMENT, OP_LIFT_BOUNDARY,
    OP_OPEN_TOPIC, OP_RESTORE, OP_SET_BOUNDARY, OP_SET_STAGE,
    SYS_DEGRADE, SYS_DISABLE, SYS_ENABLE, SYS_NOTE, SYS_RECOVER,
)
from .organizer import (
    SUMMARY_EVERY, build_summary, content_hash, extract, is_tombstoned,
    memory_id, tombstone_filters,
)
from .relationship import (
    ALL_KEYS, KEY_BOUNDARIES, KEY_COMMITMENTS, KEY_OPEN_TOPICS, KEY_STAGE,
    STAGES, apply_op, empty_state, topics_overlap,
)
from .store import Store
from .tokens import estimate_tokens, season_of, tokenize as _tokenize

RECENT_TURNS_IN_PACK = 6
ORGANIZER_VERSION = "rules-v1"


def _meta_int(store: Store, key: str, default: int = 0) -> int:
    raw = store.get_meta(key)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


class XinjiEngine:
    """一个心记库的完整引擎（L0–L4）。"""

    def __init__(self, path: str, now: Optional[Callable[[], datetime]] = None):
        self.store = Store(path)
        self._now = now or datetime.now
        self._retriever = None        # 记忆集合的缓存索引，变更后置 None
        self._retriever_mems: list[dict[str, Any]] = []
        self._profile_cache: Optional[list[str]] = None
        self._bootstrap()

    def _retriever_for(self) -> tuple["retrieval.Retriever", list[dict[str, Any]]]:
        if self._retriever is None:
            self._retriever_mems = self.store.memories()
            self._retriever = retrieval.Retriever(self._retriever_mems)
        return self._retriever, self._retriever_mems

    def _invalidate_retriever(self) -> None:
        self._retriever = None
        self._profile_cache = None

    def close(self) -> None:
        self.store.close()

    # ---- 引导与状态 ------------------------------------------------------------

    def _bootstrap(self) -> None:
        if not self.store.relationship_snapshot():
            for key in ALL_KEYS:
                self.store.put_relationship(key, empty_state()[key], 0)
        defaults = {
            "mode": "active", "organized_upto": "0", "turn_count": "0",
            "relationship_version": "0", "summarized_buckets": "",
            "organizer_version": ORGANIZER_VERSION,
        }
        for key, value in defaults.items():
            if self.store.get_meta(key) is None:
                self.store.set_meta(key, value)

    def state(self) -> dict[str, Any]:
        """当前 L2 关系档案快照。"""
        snap = self.store.relationship_snapshot()
        st = empty_state()
        for key in ALL_KEYS:
            if key in snap:
                st[key] = snap[key]["value"]
        return st

    def mode(self) -> str:
        return self.store.get_meta("mode", "active")

    def _current_turn(self) -> int:
        return _meta_int(self.store, "turn_count")

    def _tombstoned_messages(self) -> set[int]:
        return tombstone_filters(self.store.tombstones())["messages"]

    def recent_turns(self, limit: int = RECENT_TURNS_IN_PACK) -> list[dict[str, Any]]:
        """最近几轮原话（跳过已删除的消息；从尾部倒着取，避免全表扫描）。"""
        skip = self._tombstoned_messages()
        out: list[dict[str, Any]] = []
        fetch = limit + len(skip) + 20
        last_seq = 0
        while True:
            rows = self.store.conn.execute(
                "SELECT seq, payload FROM ledger WHERE type=? AND seq>?"
                " ORDER BY seq DESC LIMIT ?",
                (MESSAGE, last_seq, fetch)).fetchall()
            if not rows:
                break
            for row in rows:
                ev_payload = json.loads(row["payload"])
                if row["seq"] in skip:
                    continue
                out.append({"seq": row["seq"], "turn": ev_payload.get("turn"),
                            "role": ev_payload.get("role"),
                            "text": ev_payload.get("text", "")})
                if len(out) >= limit:
                    return list(reversed(out))
            last_seq = rows[-1]["seq"]
            if len(rows) < fetch:
                break
        return list(reversed(out))

    # ---- 写路径：听见 → 先记下来 -------------------------------------------------

    def hear(self, text: str, *, role: str = "user") -> dict[str, Any]:
        """听见一句话：原话立即进入不可变账本 L0，马上得到确认。

        摘要、事实与画像在之后由 :meth:`organize` 慢慢整理，不阻塞聊天。
        """
        turn = self._current_turn() + (1 if role == "user" else 0)
        if role == "user":
            self.store.set_meta("turn_count", str(turn))
        ev = self.store.append(EventBuilder(MESSAGE, {"role": role, "text": text, "turn": turn}))
        pending = sum(1 for m in self.store.events(_meta_int(self.store, "organized_upto"), MESSAGE)
                      if m.seq not in self._tombstoned_messages())
        return {"seq": ev.seq, "id": ev.id, "turn": turn, "ts": ev.ts, "pending": pending}

    # ---- 整理：L0 → L1 / L2 -------------------------------------------------------

    def organize(self) -> dict[str, Any]:
        """整理尚未处理的原话：派生 L1 记忆、落 L2 关系变更、生成片段摘要。

        幂等且墓碑感知：已删除的内容在这里永远不会回来。整理故障会自动
        降级（先降级，不装记得），由 :meth:`recover` 恢复后补整理。
        """
        report: dict[str, Any] = {"processed": 0, "new_memories": [], "new_ops": [],
                                  "summaries": [], "error": None}
        try:
            upto = _meta_int(self.store, "organized_upto")
            pending = [m for m in self.store.events(upto, MESSAGE)]
            done_op_seqs = self._organizer_op_evidence_seqs()
            tomb = tombstone_filters(self.store.tombstones())

            for msg in pending:
                if msg.seq in tomb["messages"]:
                    report["processed"] += 1
                    continue
                ex = extract(msg, open_topics=self._state_at(msg.seq)[KEY_OPEN_TOPICS],
                             boundaries=self._state_at(msg.seq)[KEY_BOUNDARIES],
                             commitments=self._state_at(msg.seq)[KEY_COMMITMENTS])
                # 关系变更提案 → L0 事件（幂等：同一条消息只提案一次）
                builders = []
                if msg.seq not in done_op_seqs:
                    for op in ex.ops:
                        builders.append(EventBuilder(
                            RELATIONSHIP_OP,
                            {"op": op["op"], "params": op["params"],
                             "evidence": op["params"].get("evidence", []),
                             "reason": "organizer"}))
                if builders:
                    events = self.store.append_many(builders)
                    self._apply_op_events(events)
                    report["new_ops"].extend(
                        {"op": e.payload["op"], "params": e.payload["params"]} for e in events)
                # L1 记忆（跳过被删除覆盖的派生物）
                for mem in ex.memories:
                    if is_tombstoned(mem, tomb):
                        continue
                    self.store.upsert_memory(mem)
                    report["new_memories"].append({"id": mem["id"], "kind": mem["kind"],
                                                   "topic": mem["topic"]})
                report["processed"] += 1

            new_upto = max((m.seq for m in pending), default=upto)
            report["summaries"] = self._maybe_summarize(new_upto)
            if new_upto > upto:
                self.store.set_meta("organized_upto", str(new_upto))
            if report["new_memories"] or report["new_ops"] or report["summaries"]:
                self._invalidate_retriever()
        except Exception as exc:  # 先降级，不装记得
            self.degrade(f"整理故障：{exc!r}")
            report["error"] = repr(exc)
        return report

    def _organizer_op_evidence_seqs(self) -> set[int]:
        """已落账的整理器提案覆盖的消息 seq（幂等保护）。"""
        covered: set[int] = set()
        for ev in self.store.events(0, RELATIONSHIP_OP):
            if ev.payload.get("reason") == "organizer":
                covered.update(ev.payload.get("evidence", []))
        return covered

    def _maybe_summarize(self, upto: int) -> list[str]:
        """按固定 seq 桶（每 SUMMARY_EVERY 条）生成片段摘要。

        桶的划分只取决于 seq，与整理时机无关，因此「现场整理」和
        「重放重建」永远得到同一批摘要；桶内若有已删除的消息则整桶跳过。
        """
        done = {x for x in self.store.get_meta("summarized_buckets", "").split(",") if x}
        made: list[str] = []
        max_bucket = max((upto - 1) // SUMMARY_EVERY, -1)
        tomb_msgs = self._tombstoned_messages()
        for k in range(0, max_bucket + 1):
            key = str(k)
            if key in done:
                continue
            bucket_end = (k + 1) * SUMMARY_EVERY
            if upto < bucket_end:
                continue  # 桶还没满，等后续消息
            lo, hi = k * SUMMARY_EVERY + 1, bucket_end
            if any(s in tomb_msgs for s in range(lo, hi + 1)):
                done.add(key)  # 桶里有被删除的话：整桶不摘要，且永不复活
                continue
            msgs = [m for m in self.store.events(lo - 1, MESSAGE)
                    if lo <= m.seq <= hi and m.seq not in tomb_msgs
                    and m.payload.get("role") == "user"]
            if len(msgs) >= 2:
                topics: list[str] = []
                for m in msgs:
                    st_at = self._state_at(m.seq)
                    for mem in extract(m, open_topics=st_at[KEY_OPEN_TOPICS],
                                       boundaries=st_at[KEY_BOUNDARIES],
                                       commitments=st_at[KEY_COMMITMENTS]).memories:
                        if mem.get("topic") and mem["topic"] not in topics:
                            topics.append(mem["topic"])
                summary = build_summary(msgs, topics)
                self.store.upsert_memory(summary)
                made.append(summary["id"])
            done.add(key)
        self.store.set_meta("summarized_buckets", ",".join(sorted(done, key=int)))
        return made

    def _state_at(self, seq: int) -> dict[str, Any]:
        """某句话说出时刻的关系档案（用于整理时的上下文关联）。

        规则：整理器提案按其证据消息的 seq 归位；治理操作按事件自身
        seq 归位。该规则只依赖账本内容，重放确定性不受整理时机影响。
        """
        st = empty_state()
        for ev in self.store.events(0, RELATIONSHIP_OP):
            p = ev.payload
            evidence = p.get("evidence") or []
            if evidence:
                belongs = min(evidence) <= seq
            else:
                belongs = ev.seq <= seq
            if not belongs:
                continue
            st, _ = apply_op(st, p["op"], p["params"], 0, ev.seq)
        return st

    # ---- L2 关系变更 ------------------------------------------------------------

    def _apply_op_events(self, events: list[Event]) -> None:
        version = _meta_int(self.store, "relationship_version")
        st = self.state()
        for ev in events:
            p = ev.payload
            st, record = apply_op(st, p["op"], p["params"], version + 1, ev.seq)
            version += 1
            key = record.get("key", "*")
            if key == "*":
                for k in ALL_KEYS:
                    self.store.put_relationship(k, st[k], version)
            else:
                self.store.put_relationship(key, st[key], version)
            self.store.log_relationship(version, ev.seq, record["op"],
                                        record.get("target", ""),
                                        record.get("before"), record.get("after"))
        self.store.set_meta("relationship_version", str(version))

    def relationship_op(self, op: str, params: dict[str, Any], *, reason: str = "governance",
                        evidence: Optional[list[int]] = None) -> Event:
        """显式执行一次关系变更（治理入口，同步留底）。"""
        ev = self.store.append(EventBuilder(
            RELATIONSHIP_OP, {"op": op, "params": params,
                              "evidence": evidence or [], "reason": reason}))
        self._apply_op_events([ev])
        return ev

    def set_stage(self, value: str, *, reason: str = "治理操作") -> Event:
        if value not in STAGES:
            raise ValueError(f"未知阶段 {value}，可选：{STAGES}")
        return self.relationship_op(OP_SET_STAGE, {"value": value}, reason=reason)

    def rollback_relationship(self, to_version: int) -> Event:
        """回滚关系档案到指定版本（回滚本身也是一次新增事件）。"""
        st = empty_state()
        version = 0
        for ev in self.store.events(0, RELATIONSHIP_OP):
            if version >= to_version:
                break
            st, _ = apply_op(st, ev.payload["op"], ev.payload["params"], version + 1, ev.seq)
            version += 1
        return self.relationship_op(
            OP_RESTORE, {"snapshot": {k: st[k] for k in ALL_KEYS}, "to_version": to_version},
            reason=f"回滚到 v{to_version}")

    # ---- 读路径：想好再说 ----------------------------------------------------------

    def read(self, current_input: str, *, now: Optional[datetime] = None,
             recent_limit: int = RECENT_TURNS_IN_PACK) -> dict[str, Any]:
        """组装本回合的记忆包（L3 判断在检索之后、开口之前）。"""
        mode = self.mode()
        if mode in ("disabled", "degraded"):
            return self._degraded_pack(current_input, mode, recent_limit,
                                       note="一键停用" if mode == "disabled" else "降级中")
        try:
            return self._normal_read(current_input, now, recent_limit)
        except Exception as exc:  # 先降级，不装记得
            self.degrade(f"读路径故障：{exc!r}")
            return self._degraded_pack(current_input, "degraded", recent_limit,
                                       note=f"读路径故障，已自动降级：{exc}")

    def _normal_read(self, current_input: str, now: Optional[datetime],
                     recent_limit: int) -> dict[str, Any]:
        now = now or self._now()
        st = self.state()
        turn = self._current_turn()
        # 记忆是跨轮的过去信息：当前轮刚说的话本来就在上下文里，
        # 它派生的记忆不再装包（避免把自己刚说的话当「旧事」复述）。
        cur_seqs = {ev.seq for ev in self.store.events(0, MESSAGE)
                    if ev.payload.get("role") == "user"
                    and ev.payload.get("turn") == turn}
        memories = [m for m in self.store.memories()
                    if not (set(m["provenance"]) & cur_seqs)]
        input_tokens = set(_tokenize(current_input))

        candidates: dict[str, dict[str, Any]] = {}
        eligible_ids = {m["id"] for m in memories}
        retriever, snap_mems = self._retriever_for()
        for hit, score in retriever.query(snap_mems, current_input):
            if hit["id"] not in eligible_ids:
                continue  # 缓存索引可能略新于当前轮过滤，装包前再拦一次
            # 缓存里的记忆行可能是旧的（mention_last_turn 等运行时字段），
            # 候选一律以库里的最新行为准——缓存只加速分词与打分。
            mem = self.store.memory(hit["id"]) or hit
            candidates[mem["id"]] = {"memory": mem, "score": round(score, 4),
                                     "source": "retrieval"}
        season = season_of(now.month)
        for mem, trigger in retrieval.proactive_candidates(
                memories, st[KEY_OPEN_TOPICS], {"season": season}):
            if mem["id"] in candidates:
                candidates[mem["id"]]["source"] = "proactive"
                candidates[mem["id"]]["trigger"] = trigger
            else:
                candidates[mem["id"]] = {"memory": mem, "score": 0.0,
                                         "source": "proactive", "trigger": trigger}

        allowed, suppressed = [], []
        for cand in candidates.values():
            verdict = judgment.judge(
                cand["memory"], source=cand["source"], score=cand["score"],
                input_tokens=input_tokens, state=st, current_turn=turn)
            entry = dict(cand)
            entry["verdict"] = verdict
            if verdict["decision"] == "allow":
                allowed.append(entry)
            else:
                suppressed.append(entry)

        picked = judgment.select_and_pack(allowed)
        picked_ids = [p["memory"]["id"] for p in picked]
        self.store.set_mention_turn(picked_ids, turn)
        used = sum(p["memory"]["tokens"] for p in picked)

        # 开放承诺属于「记得住」的约定：随包携带，也计入预算
        commitments = [{"what": c["what"], "topic": c.get("topic", "")}
                       for c in st[KEY_COMMITMENTS] if c.get("status") == "open"]
        used += sum(estimate_tokens(c["what"]) for c in commitments)

        return {
            "mode": "normal",
            "turn": turn,
            "input": current_input,
            "stage": st[KEY_STAGE]["value"],
            "season": season,
            "budget": {"tokens": judgment.TOKEN_BUDGET, "used": used,
                       "items": len(picked), "max_items": judgment.MAX_ITEMS},
            "memories": [self._pack_item(p) for p in picked],
            "suppressed": [self._pack_item(s, include_memory=True) for s in suppressed],
            "commitments": commitments,
            "guidance": judgment.build_guidance(picked, suppressed, st),
            "recent_turns": self.recent_turns(recent_limit),
            "profile_summary": self.profile_summary(),
            "hard_boundaries": self.hard_boundaries(),
            "note": None,
        }

    @staticmethod
    def _pack_item(entry: dict[str, Any], *, include_memory: bool = False) -> dict[str, Any]:
        mem = entry["memory"]
        item: dict[str, Any] = {
            "id": mem["id"], "kind": mem["kind"], "topic": mem["topic"],
            "content": mem["content"], "tokens": mem["tokens"],
            "score": entry.get("score", 0.0), "source": entry.get("source"),
            "judgment": entry["verdict"]["reason"],
            "detail": entry["verdict"].get("detail"),
        }
        if entry["verdict"].get("directive"):
            item["directive"] = entry["verdict"]["directive"]
        if entry.get("trigger"):
            item["trigger"] = entry["trigger"]
        if entry["verdict"].get("boundary"):
            item["boundary"] = entry["verdict"]["boundary"]
        if include_memory:
            item["provenance"] = mem["provenance"]
        return item

    def profile_summary(self) -> list[str]:
        """有效画像摘要（画像 + 偏好事实），降级包也会带上。"""
        if self._profile_cache is None:
            out = []
            for m in self.store.memories():
                if m["kind"] == "profile" or (m["kind"] == "fact" and m.get("fact_type") == "preference"):
                    out.append(m["content"])
            self._profile_cache = out
        return list(self._profile_cache)

    def hard_boundaries(self) -> list[dict[str, str]]:
        """当前有效硬边界。"""
        return [{"topic": b["topic"], "directive": b["directive"]}
                for b in self.state()[KEY_BOUNDARIES] if b.get("status") == "active"]

    def _degraded_pack(self, current_input: str, mode: str, recent_limit: int,
                       *, note: str) -> dict[str, Any]:
        """降级包：只带当前输入、最近几轮、有效画像摘要与当前硬边界。"""
        return {
            "mode": mode,
            "turn": self._current_turn(),
            "input": current_input,
            "budget": {"tokens": 0, "used": 0, "items": 0, "max_items": 0},
            "memories": [],
            "suppressed": [],
            "commitments": [],
            "guidance": "旧事已全部关闭：不引用任何可选记忆，只用最近几轮与硬边界回应。",
            "recent_turns": self.recent_turns(recent_limit),
            "profile_summary": self.profile_summary(),
            "hard_boundaries": self.hard_boundaries(),
            "note": note,
        }

    # ---- 治理 L4：查看 / 纠正 / 删除 / 导出 / 重放 / 审计 / 一键停用 -----------------

    def view(self) -> dict[str, Any]:
        st = self.state()
        upto = _meta_int(self.store, "organized_upto")
        pending = sum(1 for m in self.store.events(upto, MESSAGE)
                      if m.seq not in self._tombstoned_messages())
        return {
            "mode": self.mode(),
            "head": self.store.head(),
            "stage": st[KEY_STAGE],
            "boundaries": st[KEY_BOUNDARIES],
            "commitments": st[KEY_COMMITMENTS],
            "open_topics": st[KEY_OPEN_TOPICS],
            "memories": [{k: m[k] for k in
                          ("id", "kind", "topic", "content", "provenance", "confidence",
                           "min_stage", "corrected")} for m in self.store.memories()],
            "tombstones": self.store.tombstones(),
            "pending_organize": pending,
            "relationship_version": _meta_int(self.store, "relationship_version"),
        }

    def audit(self, *, type_: Optional[str] = None, tail: Optional[int] = None
              ) -> list[dict[str, Any]]:
        events = [{"seq": e.seq, "ts": e.ts, "type": e.type, "summary": e.summary()}
                  for e in self.store.events(0, type_)]
        return events[-tail:] if tail else events

    # 删除 ------------------------------------------------------------------------------

    def delete_messages(self, seqs: list[int], *, reason: str = "用户删除") -> dict[str, Any]:
        """删除原话：L0 里追加墓碑事件（原事件永不改写），派生记忆立即消失。"""
        seqs = sorted({int(s) for s in seqs})
        for s in seqs:
            ev = self.store.event(s)
            if ev is None or ev.type != MESSAGE:
                raise KeyError(f"seq {s} 不是消息事件")
        ev = self.store.append(EventBuilder(DELETION, {
            "kind": "message", "targets": [str(s) for s in seqs], "reason": reason}))
        self.store.add_tombstones([
            {"kind": "message", "target": str(s), "reason": reason, "seq": ev.seq}
            for s in seqs])
        # 溯源与被删消息有交集的派生记忆全部撤下（摘要引用了原话，同样撤下）
        seq_set = set(seqs)
        dropped = [m["id"] for m in self.store.memories()
                   if seq_set & set(m["provenance"])]
        self.store.drop_memories(dropped)
        self._invalidate_retriever()
        return {"event": ev.seq, "dropped_memories": dropped}

    def delete_memory(self, memory_id: str, *, reason: str = "用户删除") -> dict[str, Any]:
        """删除一条整理后的记忆：墓碑按 id 与内容哈希登记——整理器是
        确定性的，补整理与重放都派生出同一个 id，因此无法复活。"""
        mem = self.store.memory(memory_id)
        if mem is None:
            raise KeyError(f"找不到记忆 {memory_id}")
        ev = self.store.append(EventBuilder(DELETION, {
            "kind": "memory",
            "targets": [{"id": mem["id"], "content_hash": mem["content_hash"],
                         "provenance": mem["provenance"]}],
            "reason": reason}))
        self.store.add_tombstones([{
            "kind": "memory", "target": mem["id"], "content_hash": mem["content_hash"],
            "provenance": mem["provenance"], "reason": reason, "seq": ev.seq}])
        self.store.drop_memories([mem["id"]])
        self._invalidate_retriever()
        return {"event": ev.seq, "dropped_memories": [mem["id"]]}

    def delete_topic(self, topic: str, *, reason: str = "用户删除") -> dict[str, Any]:
        """删除一个话题下的所有记忆，并关闭对应的开放话题。"""
        matching = [m for m in self.store.memories()
                    if m["topic"] and topics_overlap(m["topic"], topic)]
        ev = self.store.append(EventBuilder(DELETION, {
            "kind": "topic", "targets": [topic], "reason": reason}))
        self.store.add_tombstones([{"kind": "topic", "target": topic,
                                    "reason": reason, "seq": ev.seq}])
        self.store.drop_memories([m["id"] for m in matching])
        self._invalidate_retriever()
        st = self.state()
        hit = next((t for t in st[KEY_OPEN_TOPICS]
                    if t["status"] == "open" and topics_overlap(t["topic"], topic)), None)
        if hit is not None:
            self.relationship_op(OP_CLOSE_TOPIC, {"ref": hit["topic"]},
                                 reason=f"话题已删除（{reason}）")
        return {"event": ev.seq,
                "dropped_memories": [m["id"] for m in matching],
                "closed_topic": hit["id"] if hit else None}

    def correct_memory(self, memory_id: str, new_content: str, *,
                       reason: str = "用户纠正") -> dict[str, Any]:
        """纠正一条记忆：追加纠正事件，保留旧内容在账本里可审计。"""
        mem = self.store.memory(memory_id)
        if mem is None:
            raise KeyError(f"找不到记忆 {memory_id}")
        ev = self.store.append(EventBuilder(MEMORY_CORRECTION, {
            "memory_id": memory_id, "old_content": mem["content"],
            "new_content": new_content, "reason": reason}))
        self.store.update_memory_content(
            memory_id, new_content, content_hash(new_content), estimate_tokens(new_content))
        self._invalidate_retriever()
        return {"event": ev.seq, "memory": memory_id}

    # 导出 ------------------------------------------------------------------

    def export(self, *, full: bool = False) -> dict[str, Any]:
        """导出账本与当前状态。默认安全导出：已删除的原话以墓碑占位。"""
        tomb_msgs = self._tombstoned_messages()
        events = []
        for ev in self.store.events():
            payload = dict(ev.payload)
            if not full and ev.type == MESSAGE and ev.seq in tomb_msgs:
                payload["text"] = "［已删除］"
            item = {"seq": ev.seq, "id": ev.id, "ts": ev.ts, "type": ev.type,
                    "payload": payload}
            if full:
                item["prev_hash"] = ev.prev_hash
                item["hash"] = ev.hash
            events.append(item)
        return {
            "format": "xinji.export/v1",
            "exported_at": now_iso(),
            "full": full,
            "head": self.store.head(),
            "mode": self.mode(),
            "events": events,
            "memories": self.store.memories(),
            "tombstones": self.store.tombstones(),
        }

    # 重放 ------------------------------------------------------------------

    def replay(self, *, verify: bool = True) -> dict[str, Any]:
        """从 L0 账本重放整颗心：重建 L1/L2/墓碑/模式，并与现场比对。

        这是「删除可验证」的验收入口：重放后的状态若与现场一致，
        说明没有任何已删除内容复活、也没有任何状态凭空出现。
        比对前会先把现场欠下的整理补齐（幂等的「补整理」），保证
        两侧口径一致——这一步本身也在验证「补整理不让旧内容回来」。
        """
        if verify:
            self.organize()
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="xinji-replay-")
        os.close(fd)
        try:
            os.remove(tmp_path)
            replay_store = Store(tmp_path)
            try:
                self.store.copy_ledger_into(replay_store)
                fold_and_organize(replay_store)
                replay_engine = XinjiEngine.__new__(XinjiEngine)
                replay_engine.store = replay_store
                replay_engine._now = self._now
                replay_engine._bootstrap()
                result: dict[str, Any] = {"ok": True, "differences": []}
                if verify:
                    result = compare_states(self, replay_engine)
                return result
            finally:
                replay_store.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp_path + suffix)
                except FileNotFoundError:
                    pass

    def rebuild(self) -> None:
        """把当前库的派生状态从账本完整重建（修复路径，账本不动）。"""
        fold_and_organize(self.store)
        self._bootstrap()
        self._invalidate_retriever()

    def verify_ledger(self) -> list[int]:
        """校验账本哈希链，返回损坏的 seq（空列表 = 完好）。"""
        return self.store.verify_chain()

    # 一键停用 / 降级 ------------------------------------------------------------

    def disable(self, *, reason: str = "一键停用") -> dict[str, Any]:
        """一键停用：读路径立刻降级，聊天照常，账本继续记录。"""
        ev = self.store.append(EventBuilder(SYSTEM, {"kind": SYS_DISABLE, "reason": reason}))
        self.store.set_meta("mode", "disabled")
        return {"event": ev.seq, "mode": "disabled"}

    def enable(self, *, reason: str = "恢复启用") -> dict[str, Any]:
        ev = self.store.append(EventBuilder(SYSTEM, {"kind": SYS_ENABLE, "reason": reason}))
        self.store.set_meta("mode", "active")
        return {"event": ev.seq, "mode": "active"}

    def degrade(self, reason: str) -> dict[str, Any]:
        ev = self.store.append(EventBuilder(SYSTEM, {"kind": SYS_DEGRADE, "reason": reason}))
        self.store.set_meta("mode", "degraded")
        return {"event": ev.seq, "mode": "degraded"}

    def recover(self, *, reason: str = "故障恢复") -> dict[str, Any]:
        """恢复：回到正常读路径，并把停摆期间欠下的整理补上。"""
        ev = self.store.append(EventBuilder(SYSTEM, {"kind": SYS_RECOVER, "reason": reason}))
        self.store.set_meta("mode", "active")
        report = self.organize()
        return {"event": ev.seq, "mode": "active", "organize": report}

    def note(self, text: str) -> dict[str, Any]:
        ev = self.store.append(EventBuilder(SYSTEM, {"kind": SYS_NOTE, "note": text}))
        return {"event": ev.seq}


# ==== 重放：从账本折叠出全部派生状态 ============================================


def fold_and_organize(store: Store) -> None:
    """重放核心：清空派生表，按账本事件顺序重建 L2 / 墓碑 / 模式 / L1。

    顺序：先折叠全部关系变更、墓碑、纠正与系统事件（确定 L2 与删除
    覆盖范围），再整理所有未删除的原话（跳过墓碑覆盖的派生物），
    最后重放纠正。与「现场整理」使用完全相同的规则函数。
    """
    store.clear_derived()
    st = empty_state()
    for key in ALL_KEYS:  # 先落一份空档案，保证四个分片始终齐全
        store.put_relationship(key, st[key], 0)
    version = 0
    messages: list[Event] = []
    tomb_rows: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    mode = "active"
    user_turns = 0

    for ev in store.events():
        if ev.type == MESSAGE:
            messages.append(ev)
            if ev.payload.get("role") == "user":
                user_turns += 1
        elif ev.type == RELATIONSHIP_OP:
            p = ev.payload
            st, record = apply_op(st, p["op"], p["params"], version + 1, ev.seq)
            version += 1
            key = record.get("key", "*")
            if key == "*":
                for k in ALL_KEYS:
                    store.put_relationship(k, st[k], version)
            else:
                store.put_relationship(key, st[key], version)
            store.log_relationship(version, ev.seq, record["op"], record.get("target", ""),
                                   record.get("before"), record.get("after"))
        elif ev.type == DELETION:
            p = ev.payload
            for tgt in p["targets"]:
                if p["kind"] == "memory":
                    tomb_rows.append({"kind": "memory", "target": tgt["id"],
                                      "content_hash": tgt.get("content_hash", ""),
                                      "provenance": tgt.get("provenance", []),
                                      "reason": p.get("reason", ""), "seq": ev.seq})
                else:
                    tomb_rows.append({"kind": p["kind"], "target": str(tgt),
                                      "reason": p.get("reason", ""), "seq": ev.seq})
        elif ev.type == MEMORY_CORRECTION:
            corrections.append(ev.payload)
        elif ev.type == SYSTEM:
            kind = ev.payload.get("kind")
            if kind == SYS_DISABLE:
                mode = "disabled"
            elif kind == SYS_DEGRADE:
                mode = "degraded"
            elif kind in (SYS_ENABLE, SYS_RECOVER):
                mode = "active"

    store.add_tombstones(tomb_rows)
    store.set_meta("mode", mode)
    store.set_meta("turn_count", str(user_turns))
    store.set_meta("relationship_version", str(version))
    store.set_meta("organized_upto", "0")
    store.set_meta("summarized_buckets", "")
    store.set_meta("organizer_version", ORGANIZER_VERSION)

    tomb = tombstone_filters(tomb_rows)
    max_seq = 0
    for msg in messages:
        max_seq = max(max_seq, msg.seq)
        if msg.seq in tomb["messages"]:
            continue
        ex = _extract_at(store, msg, st)
        for mem in ex.memories:
            if is_tombstoned(mem, tomb):
                continue
            store.upsert_memory(mem)
    _replay_summaries(store, tomb, max_seq)
    for c in corrections:
        if store.memory(c["memory_id"]) is not None:
            store.update_memory_content(
                c["memory_id"], c["new_content"],
                content_hash(c["new_content"]), estimate_tokens(c["new_content"]))
    store.set_meta("organized_upto", str(max_seq))


def _extract_at(store: Store, msg: Event, final_state: dict[str, Any]):
    """重放时按「该句说出时刻」的关系档案提取（与现场整理同一规则）。"""
    st_at = _replay_state_at(store, msg.seq, final_state)
    return extract(msg, open_topics=st_at[KEY_OPEN_TOPICS],
                   boundaries=st_at[KEY_BOUNDARIES],
                   commitments=st_at[KEY_COMMITMENTS])


def _replay_state_at(store: Store, seq: int, _final: dict[str, Any]) -> dict[str, Any]:
    st = empty_state()
    for ev in store.events(0, RELATIONSHIP_OP):
        p = ev.payload
        evidence = p.get("evidence") or []
        belongs = (min(evidence) <= seq) if evidence else (ev.seq <= seq)
        if belongs:
            st, _ = apply_op(st, p["op"], p["params"], 0, ev.seq)
    return st


def _replay_summaries(store: Store, tomb: dict[str, Any], max_seq: int) -> None:
    """重放时按同样的 seq 桶规则重建片段摘要。"""
    done: set[str] = set()
    tomb_msgs = tomb["messages"]
    made = 0
    for k in range(0, max((max_seq - 1) // SUMMARY_EVERY, -1) + 1):
        key = str(k)
        lo, hi = k * SUMMARY_EVERY + 1, (k + 1) * SUMMARY_EVERY
        if hi > max_seq:
            break
        done.add(key)
        if any(s in tomb_msgs for s in range(lo, hi + 1)):
            continue
        msgs = [m for m in store.events(lo - 1, MESSAGE)
                if lo <= m.seq <= hi and m.seq not in tomb_msgs
                and m.payload.get("role") == "user"]
        if len(msgs) >= 2:
            topics: list[str] = []
            for m in msgs:
                for mem in _extract_at(store, m, {}).memories:
                    if mem.get("topic") and mem["topic"] not in topics:
                        topics.append(mem["topic"])
            store.upsert_memory(build_summary(msgs, topics))
            made += 1
    store.set_meta("summarized_buckets", ",".join(sorted(done, key=int)))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def compare_states(live: XinjiEngine, replayed: XinjiEngine) -> dict[str, Any]:
    """逐项比对现场状态与重放状态（忽略运行时缓存字段）。"""
    diffs: list[dict[str, Any]] = []

    def mem_norm(m: dict[str, Any]) -> dict[str, Any]:
        return {k: m.get(k) for k in ("id", "kind", "topic", "content", "content_hash",
                                      "provenance", "min_stage", "confidence", "tokens",
                                      "derived_at", "corrected", "fact_type")}

    a = sorted((mem_norm(m) for m in live.store.memories()), key=_canonical)
    b = sorted((mem_norm(m) for m in replayed.store.memories()), key=_canonical)
    if _canonical(a) != _canonical(b):
        diffs.append({"field": "memories", "live": a, "replay": b})

    ra, rb = live.store.relationship_snapshot(), replayed.store.relationship_snapshot()
    if _canonical(ra) != _canonical(rb):
        diffs.append({"field": "relationship", "live": ra, "replay": rb})

    ta = sorted(live.store.tombstones(), key=_canonical)
    tb = sorted(replayed.store.tombstones(), key=_canonical)
    if _canonical(ta) != _canonical(tb):
        diffs.append({"field": "tombstones", "live": ta, "replay": tb})

    for key in ("mode", "organized_upto", "turn_count", "relationship_version",
                "summarized_buckets"):
        va, vb = live.store.get_meta(key), replayed.store.get_meta(key)
        if va != vb:
            diffs.append({"field": f"meta.{key}", "live": va, "replay": vb})

    return {"ok": not diffs, "differences": diffs}
