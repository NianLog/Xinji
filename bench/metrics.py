"""评测指标的累计与聚合。

六项指标（对应 README 评测基线一节）：

1. **事实召回率** recall：召回探针轮中，期望事实出现在上下文里的比例
   （探针级：该轮期望的事实全部命中才算命中）。
2. **提及准确率** mention：召回探针（应暴露）与越界/复活探针（不应暴露）
   合并的探针级正确率。
3. **越界率** violation：边界探针轮中，被边界覆盖的事实出现在上下文的比例。
4. **删除后复活率** resurrection：删除 + 强制重建后，被删事实出现在上下文的比例。
5. **关系阶段迁移正确率** stage：脚本化阶段变化与策略记录阶段的一致比例
   （仅心记支持；baseline 无此机制，记 N/A）。
6. **成本 / 延迟**：上下文 token（均值与 p95）与选择耗时（均值与 p95，ms）。

判分口径：暴露 = 上下文单元包含事实匹配词（合成基准：精确子串；
LoCoMo：精确子串或证据词重叠 ≥ 50%）；判分前先剔除「当前输入本身」——
用户提问引用被删事实的词不算暴露，旧记忆被翻出来才算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TurnEval:
    """一轮的判分结果。"""

    recall_total: int = 0
    recall_hits: int = 0
    forbidden_total: int = 0
    forbidden_violations: int = 0
    after_deletion: bool = False       # 本轮是否为「删除后复活」探针
    stage_expected: Optional[str] = None
    stage_got: Optional[str] = None
    tokens: int = 0
    ms: float = 0.0
    phenomena: list[str] = field(default_factory=list)  # 现象标签（缺陷定位用）

    @property
    def recall_probe_ok(self) -> Optional[bool]:
        if self.recall_total == 0:
            return None
        return self.recall_hits == self.recall_total

    @property
    def forbidden_probe_ok(self) -> Optional[bool]:
        if self.forbidden_total == 0:
            return None
        return self.forbidden_violations == 0


@dataclass
class ConversationEval:
    turns: list[TurnEval] = field(default_factory=list)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[min(len(xs) - 1, int(0.95 * len(xs)))]


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def summarize(evals: list[ConversationEval], *, supports_stage: bool) -> dict[str, Any]:
    """把多段对话的逐轮评测聚合成六项指标。

    macro = 各对话先算比率再取平均；micro = 全部探针合并计数。
    """
    macro_recall, macro_viol, macro_res = [], [], []
    micro = {"recall_h": 0, "recall_t": 0, "viol": 0, "viol_t": 0,
             "res_viol": 0, "res_t": 0, "correct": 0, "probes": 0}
    breakdown: dict[str, dict[str, int]] = {}
    stage_ok = stage_total = 0
    tokens: list[float] = []
    mss: list[float] = []

    def _acc(ph: str, *, recall_h: int = 0, recall_t: int = 0,
             viol: int = 0, viol_t: int = 0) -> None:
        b = breakdown.setdefault(
            ph, {"recall_h": 0, "recall_t": 0, "viol": 0, "viol_t": 0})
        b["recall_h"] += recall_h
        b["recall_t"] += recall_t
        b["viol"] += viol
        b["viol_t"] += viol_t

    for ev in evals:
        conv_rh = conv_rt = conv_v = conv_vt = conv_rv = conv_rt2 = 0
        for t in ev.turns:
            conv_rh += t.recall_hits
            conv_rt += t.recall_total
            conv_v += t.forbidden_violations
            conv_vt += t.forbidden_total
            micro["recall_h"] += t.recall_hits
            micro["recall_t"] += t.recall_total
            micro["viol"] += t.forbidden_violations
            micro["viol_t"] += t.forbidden_total
            if t.recall_total:
                micro["correct"] += int(t.recall_hits == t.recall_total)
                micro["probes"] += 1
            if t.forbidden_total:
                micro["correct"] += int(t.forbidden_violations == 0)
                micro["probes"] += 1
                if t.after_deletion:
                    conv_rt2 += 1
                    conv_rv += int(t.forbidden_violations > 0)
                    micro["res_t"] += 1
                    micro["res_viol"] += int(t.forbidden_violations > 0)
            for ph in t.phenomena:
                _acc(ph, recall_h=t.recall_hits, recall_t=t.recall_total,
                     viol=t.forbidden_violations, viol_t=t.forbidden_total)
            if t.stage_expected is not None:
                stage_total += 1
                stage_ok += int(t.stage_got == t.stage_expected)
            tokens.append(float(t.tokens))
            mss.append(t.ms)

        if conv_rt:
            macro_recall.append(conv_rh / conv_rt)
        if conv_vt:
            macro_viol.append(conv_v / conv_vt)
        if conv_rt2:
            macro_res.append(conv_rv / conv_rt2)

    return {
        "recall_macro": _mean(macro_recall),
        "recall_micro": (micro["recall_h"] / micro["recall_t"]) if micro["recall_t"] else None,
        "violation_macro": _mean(macro_viol),
        "violation_micro": (micro["viol"] / micro["viol_t"]) if micro["viol_t"] else None,
        "mention_accuracy": (micro["correct"] / micro["probes"]) if micro["probes"] else None,
        "resurrection_macro": _mean(macro_res),
        "resurrection_micro": (micro["res_viol"] / micro["res_t"]) if micro["res_t"] else None,
        "stage_accuracy": (stage_ok / stage_total) if (supports_stage and stage_total) else None,
        "stage_total": stage_total,
        "avg_tokens": _mean(tokens),
        "p95_tokens": _p95(tokens),
        "avg_ms": _mean(mss),
        "p95_ms": _p95(mss),
        "probe_turns": micro["probes"],
        "phenomena_breakdown": {
            ph: {
                "recall": (b["recall_h"] / b["recall_t"]) if b["recall_t"] else None,
                "recall_total": b["recall_t"],
                "violation": (b["viol"] / b["viol_t"]) if b["viol_t"] else None,
                "violation_total": b["viol_t"],
            } for ph, b in sorted(breakdown.items())
        },
    }
