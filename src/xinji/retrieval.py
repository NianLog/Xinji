"""检索层：BM25 词袋召回 + 开放话题的时节主动扫描。

心记不替代 RAG：检索只负责「找得到」；「该不该说」完全交给 L3
（:mod:`xinji.judgment`）。因此这里刻意简单、可解释、零依赖——
硬边界过滤发生在检索**之后**、排序之前，见 judgment 模块。
"""

from __future__ import annotations

from typing import Any, Optional

from .tokens import tokenize

K1 = 1.5
B = 0.75


def bm25_scores(query_tokens: list[str], docs: list[dict[str, Any]]) -> list[float]:
    """对 docs 里每篇（dict 须含 ``tokens`` 词表）计算 BM25 得分。"""
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n
    avgdl = sum(len(d["tokens"]) for d in docs) / n or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d["tokens"]):
            df[t] = df.get(t, 0) + 1
    scores = []
    qset = set(query_tokens)
    for d in docs:
        tf: dict[str, int] = {}
        for t in d["tokens"]:
            if t in qset:
                tf[t] = tf.get(t, 0) + 1
        score = 0.0
        dl = len(d["tokens"]) or 1
        for t, f in tf.items():
            idf = (n - df[t] + 0.5) / (df[t] + 0.5)
            score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / avgdl))
        scores.append(score)
    return scores


def retrieve(query: str, memories: list[dict[str, Any]], top_k: int = 20
             ) -> list[tuple[dict[str, Any], float]]:
    """按当前输入召回相关记忆（得分 > 0 才算证据；0 分 = 无证据）。"""
    q = tokenize(query)
    docs = [{"m": m, "tokens": tokenize(f"{m.get('topic', '')} {m['content']}")}
            for m in memories]
    scores = bm25_scores(q, docs)
    ranked = sorted(zip(memories, scores), key=lambda x: x[1], reverse=True)
    return [(m, s) for m, s in ranked if s > 0][:top_k]


class Retriever:
    """带缓存的 BM25 检索器：文档分词、IDF 与平均长度只算一次。

    记忆规模到数千条时，每次查询重建词表的开销不可接受；
    调用方在记忆变更（整理 / 删除 / 纠正）后换一个新实例即可。
    """

    def __init__(self, memories: list[dict[str, Any]]):
        self.docs = [tokenize(f"{m.get('topic', '')} {m['content']}") for m in memories]
        self.n = len(self.docs)
        self.avgdl = (sum(len(d) for d in self.docs) / self.n) if self.n else 1.0
        df: dict[str, int] = {}
        for d in self.docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        self.df = df

    def query(self, memories: list[dict[str, Any]], text: str, top_k: int = 20
              ) -> list[tuple[dict[str, Any], float]]:
        """对查询打分并返回 top-k（memories 与构造时同序，用于取回记忆行）。"""
        if self.n == 0:
            return []
        q = set(tokenize(text))
        if not q:
            return []
        scores = []
        for i, doc in enumerate(self.docs):
            dl = len(doc) or 1
            score = 0.0
            for t in q:
                f = doc.count(t)
                if not f:
                    continue
                df = self.df.get(t, 0)
                idf = (self.n - df + 0.5) / (df + 0.5)
                score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avgdl))
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        return [(memories[i], s) for s, i in scores[:top_k]]


def proactive_candidates(memories: list[dict[str, Any]], open_topics: list[dict[str, Any]],
                         context: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """时节触发的主动候选：开放话题的 trigger 命中当前时节。

    返回 ``(记忆, 触发说明)``。是否允许开口由 L3 判断——这里只是
    「时候到了，有一个话题可能值得想起」，不是「应该提起」。
    话题匹配优先精确相等，其次取重叠最多的记忆（多个话题共享
    bigram 时——如「青岛看海」与「洱海看海」——不能拿第一个凑数）。
    """
    season: Optional[str] = context.get("season")
    out: list[tuple[dict[str, Any], str]] = []
    if not season:
        return out
    for t in open_topics:
        if t.get("status") != "open":
            continue
        trigger = t.get("trigger") or {}
        if trigger.get("season") != season:
            continue
        reason = f"时节触发：{t['topic']}（约定的季节到了）"
        candidates = [m for m in memories
                      if m.get("topic") and m["kind"] != "summary"]
        exact = [m for m in candidates if m["topic"] == t["topic"]]
        if exact:
            hit = exact[0]
        else:
            scored = sorted(
                ((_overlap_score(m["topic"], t["topic"]), m) for m in candidates),
                key=lambda x: -x[0])
            hit = next((m for s, m in scored if s > 0), None)
        if hit is None:
            hit = next((m for m in memories if m["kind"] == "episode"
                        and _in_text(t["topic"], m["content"])), None)
        if hit is not None:
            out.append((hit, reason))
    return out


def _overlap_score(a: str, b: str) -> int:
    return len(set(tokenize(a)) & set(tokenize(b)))


def _in_text(needle: str, text: str) -> bool:
    tn = set(tokenize(needle))
    return bool(tn & set(tokenize(text)))
