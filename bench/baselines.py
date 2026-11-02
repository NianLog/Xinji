"""被评测的记忆策略：心记 + README 定义的四个 baseline。

所有策略实现同一接口，输入相同的对话流与治理动作，输出「交给下游
模型的上下文」。评测只比较**记忆层的选择质量**，不比较模型：

- ``observe``：听进一条用户消息（心记会入账并按节奏整理）；
- ``delete_message``：治理删除（心记走 L0 墓碑；baseline 走各自的天真删除）；
- ``rebuild``：强制重建派生层（模拟补整理 / 重启重建——复活探针的压力源）；
- ``context``：为当前输入组装上下文，返回（暴露单元, 总 token 成本）。

暴露单元用于判分（事实是否被翻出来）；token 成本包含全部会交给模型
的内容（心记额外含边界与分寸提示，报告中已注明）。
"""

from __future__ import annotations

import tempfile
import os
import time
from collections import deque
from datetime import datetime
from typing import Any, Optional

from xinji import XinjiEngine
from xinji.events import MESSAGE, Event
from xinji.organizer import extract
from xinji.tokens import estimate_tokens, tokenize

WINDOW_SIZE = 10        # 滑动窗口基线保留的最近消息数
SUMMARY_RECENT = 8      # 摘要基线在摘要之外保留的最近消息数
DIGEST_CHARS = 40       # 摘要基线每条消息压缩保留的字符数
OAG_TOP_K = 20          # OAG 基线单次最多给出的结构化条目


def _dt(month: int) -> datetime:
    return datetime(2026, month, 15)


class Strategy:
    """策略基类。"""

    name = "base"
    supports_stage = False
    query_independent = False   # True：上下文与当前输入无关（可整段缓存）

    def observe(self, text: str, month: int) -> Any:
        raise NotImplementedError

    def set_stage(self, stage: str) -> None:
        raise NotImplementedError

    def stage(self) -> Optional[str]:
        return None

    def delete_message(self, handle: Any) -> None:
        raise NotImplementedError

    def rebuild(self) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        """批量写入后的整理时机（心记用；baseline 无操作）。"""

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        """返回 (暴露单元列表, 上下文总 token 估算)。"""
        raise NotImplementedError

    def close(self) -> None:
        pass


class FullContextStrategy(Strategy):
    """基线 1 · 全量长上下文：能塞多少旧对话就塞多少。"""

    name = "full-context"
    query_independent = True

    def __init__(self):
        self.raw: list[str] = []
        self._tokens: list[int] = []
        self._stage: Optional[str] = None

    def observe(self, text: str, month: int) -> Any:
        self.raw.append(text)
        self._tokens.append(estimate_tokens(text))
        return len(self.raw) - 1

    def set_stage(self, stage: str) -> None:
        self._stage = stage

    def stage(self) -> Optional[str]:
        return self._stage

    def delete_message(self, handle: Any) -> None:
        pass  # 原始转录不可变：删除对它无效（这正是要暴露的失败模式）

    def rebuild(self) -> None:
        pass

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        return list(self.raw), sum(self._tokens)


class SlidingWindowStrategy(Strategy):
    """基线 2 · 滑动窗口：只保留最近 N 条消息。"""

    name = "sliding-window"
    query_independent = True

    def __init__(self, n: int = WINDOW_SIZE):
        self.recent: deque[str] = deque(maxlen=n)

    def observe(self, text: str, month: int) -> Any:
        self.recent.append(text)
        return len(self.recent) - 1  # 句柄仅用于统一接口；窗口策略的删除是空操作

    def set_stage(self, stage: str) -> None:
        pass

    def delete_message(self, handle: Any) -> None:
        pass  # 窗口外的内容早已自然滑出；窗口内的不做特殊处理

    def rebuild(self) -> None:
        pass

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        units = list(self.recent)
        return units, sum(estimate_tokens(u) for u in units)


class SummaryCompressionStrategy(Strategy):
    """基线 3 · 摘要压缩：历史压成分阶段摘要 + 最近几轮原文。

    摘要定期从**完整原始记录**重建（常见的工程实现）；「天真删除」
    只删摘要里的行——重建时被删内容随原始记录一起回来（复活）。
    """

    name = "summary"
    query_independent = True

    def __init__(self):
        self.raw: list[str] = []
        self.digest: list[str] = []
        self.recent: deque[str] = deque(maxlen=SUMMARY_RECENT)

    @staticmethod
    def _trunc(text: str) -> str:
        return text[:DIGEST_CHARS] + ("…" if len(text) > DIGEST_CHARS else "")

    def observe(self, text: str, month: int) -> Any:
        self.raw.append(text)
        self.digest.append(self._trunc(text))
        self.recent.append(text)
        return len(self.raw) - 1

    def set_stage(self, stage: str) -> None:
        pass

    def delete_message(self, handle: Any) -> None:
        removed = self.raw[handle]
        head = removed[:DIGEST_CHARS]
        self.digest = [d for d in self.digest
                       if d != head and d != head + "…"]

    def rebuild(self) -> None:
        # 从完整原始记录重建摘要：被删的内容随之复活（无墓碑机制）
        self.digest = [self._trunc(t) for t in self.raw]

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        units = self.digest + list(self.recent)
        return units, sum(estimate_tokens(u) for u in units)


class OagStrategy(Strategy):
    """基线 4 · OAG 式结构化记忆：实体/话题键控的事实与承诺，无开口判断。

    与心记共用同一个抽取器（公平）；区别是没有边界、阶段与墓碑——
    结构化条目按话题键检索，重建时从原始记录重新抽取（复活）。
    """

    name = "oag"

    def __init__(self):
        self.raw: list[str] = []
        self.items: list[dict[str, Any]] = []   # {keys, content, prov}
        self.promises: list[dict[str, Any]] = []

    @staticmethod
    def _items_of(idx: int, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        ev = Event(seq=idx + 1, id="", ts="", type=MESSAGE,
                   payload={"role": "user", "text": text, "turn": idx + 1},
                   prev_hash="", hash="")
        ex = extract(ev)
        items, promises = [], []
        for mem in ex.memories:
            if mem["kind"] == "summary":
                continue
            items.append({"keys": set(tokenize(f"{mem.get('topic', '')} {mem['content']}")),
                          "content": mem["content"], "prov": idx})
        for op in ex.ops:
            if op["op"] == "add_commitment":
                promises.append({"content": op["params"]["what"], "prov": idx})
        return items, promises

    def observe(self, text: str, month: int) -> Any:
        idx = len(self.raw)
        self.raw.append(text)
        items, promises = self._items_of(idx, text)
        self.items.extend(items)
        self.promises.extend(promises)
        return idx

    def set_stage(self, stage: str) -> None:
        pass  # 结构化记忆不跟踪关系阶段

    def delete_message(self, handle: Any) -> None:
        self.items = [it for it in self.items if it["prov"] != handle]
        self.promises = [p for p in self.promises if p["prov"] != handle]

    def rebuild(self) -> None:
        # 从原始记录重新抽取：被删条目复活（无墓碑机制）
        self.items.clear()
        self.promises.clear()
        for idx, text in enumerate(self.raw):
            items, promises = self._items_of(idx, text)
            self.items.extend(items)
            self.promises.extend(promises)

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        q = set(tokenize(query))
        if any(w in query for w in ("约定", "承诺", "说好的")):
            units = [p["content"] for p in self.promises]
        else:
            scored = []
            for it in self.items:
                overlap = len(q & it["keys"])
                if overlap > 0:
                    scored.append((overlap, it["content"]))
            scored.sort(key=lambda x: -x[0])
            units = [c for _, c in scored[:OAG_TOP_K]]
        return units, sum(estimate_tokens(u) for u in units)


class XinjiStrategy(Strategy):
    """心记（被测系统）：hear → organize → read 全链路 + L0 墓碑删除。"""

    name = "xinji"
    supports_stage = True

    def __init__(self, db_dir: str, organize_every: int = 1):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="xj-", dir=db_dir)
        os.close(fd)
        os.remove(path)
        self.path = path
        self.eng = XinjiEngine(path)
        self.organize_every = max(1, organize_every)
        self._since = 0

    def observe(self, text: str, month: int) -> Any:
        ack = self.eng.hear(text)
        self._since += 1
        if self._since >= self.organize_every:
            self.eng.organize()
            self._since = 0
        return ack["seq"]

    def flush(self) -> None:
        self.eng.organize()
        self._since = 0

    def set_stage(self, stage: str) -> None:
        self.eng.set_stage(stage, reason="评测脚本（关系先验）")

    def stage(self) -> Optional[str]:
        return self.eng.state()["stage"]["value"]

    def delete_message(self, handle: Any) -> None:
        self.eng.delete_messages([int(handle)], reason="评测脚本（用户请求删除）")
        self.eng.organize()
        self._since = 0

    def rebuild(self) -> None:
        self.eng.rebuild()

    def context(self, query: str, month: int) -> tuple[list[str], int]:
        pack = self.eng.read(query, now=_dt(month))
        units = [m["content"] for m in pack["memories"]]
        units += [t["text"] for t in pack["recent_turns"]]
        units += list(pack["profile_summary"])
        units += [c["what"] for c in pack.get("commitments", [])]
        # 成本口径：交给模型的全部内容（含边界与分寸提示，不含被压下候选）
        cost_units = units + [b["directive"] for b in pack["hard_boundaries"]] \
            + [pack["guidance"]]
        return units, sum(estimate_tokens(u) for u in cost_units)

    def close(self) -> None:
        self.eng.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except FileNotFoundError:
                pass


def make_strategies(db_dir: str, names: Optional[list[str]] = None,
                    organize_every: int = 1) -> list[Strategy]:
    """按名字构建被测策略列表（默认全部五个）。"""
    factories = {
        "full-context": FullContextStrategy,
        "sliding-window": SlidingWindowStrategy,
        "summary": SummaryCompressionStrategy,
        "oag": OagStrategy,
        "xinji": lambda: XinjiStrategy(db_dir, organize_every=organize_every),
    }
    names = names or list(factories)
    return [factories[n]() for n in names]
