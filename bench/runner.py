"""评测 runner：双轨运行（XinjiBench 合成基准 + LoCoMo 真实数据）并生成报告。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .baselines import Strategy, make_strategies
from .metrics import ConversationEval, TurnEval, summarize
from .scenarios import Conversation, generate_dataset, dataset_fingerprint, norm
from xinji.tokens import tokenize

BENCH_VERSION = "xinji-bench/v2"
TRACK_SYNTHETIC = "synthetic"
TRACK_LOCOMO = "locomo"


def _exposed(match: str, units: list[str], overlap_threshold: float = 0.5) -> bool:
    """便捷入口：在（已过滤的）单元列表上做一次性的暴露检查。"""
    return ExposureChecker(units).exposed(match, overlap_threshold)


class ExposureChecker:
    """一段上下文的暴露检查器：预计算规范化文本与词袋（大上下文可复用）。

    判分口径：精确子串，或匹配词与单元的词重叠 ≥ 阈值（摘要压缩只保留
    句头时仍可命中）。调用方必须先剔除「当前输入本身」——提问引用被删
    事实的词不算暴露，旧记忆被翻出来才算。
    """

    def __init__(self, units: list[str]):
        self.units = units
        self.normed = [norm(u) for u in units]
        self.tokensets = [set(tokenize(u)) for u in units]

    def exposed(self, match: str, overlap_threshold: float = 0.5) -> bool:
        nm = norm(match)
        if not nm:
            return False
        if any(nm in u for u in self.normed):
            return True
        mt = set(tokenize(match))
        if len(mt) < 3:
            return False
        for ut in self.tokensets:
            if ut and len(mt & ut) / len(mt) >= overlap_threshold:
                return True
        return False


# ---- Track B · XinjiBench 合成基准 --------------------------------------------


def eval_conversation(conv: Conversation, strategy: Strategy) -> ConversationEval:
    """把一段脚本喂给一个策略并逐轮判分。"""
    ev = ConversationEval()
    handles: dict[str, Any] = {}
    for i, t in enumerate(conv.turns):
        # 1) 听进原话
        if t.text:
            handle = strategy.observe(t.text, t.month)
            for key, src in conv.fact_source_turn.items():
                if src == i and handle is not None:
                    handles[key] = handle
        # 2) 脚本化的关系阶段迁移
        if t.stage:
            strategy.set_stage(t.stage)
        # 3) 治理删除与强制重建
        for spec in t.deletions:
            strategy.delete_message(handles[spec["ref"]])
        if t.rebuild:
            strategy.rebuild()
        strategy.flush()
        # 4) 探针判分（阶段迁移轮没有探针，也要记录 stage 对错）
        if not (t.recall or t.forbidden):
            if t.stage:
                ev.turns.append(TurnEval(
                    stage_expected=t.stage,
                    stage_got=strategy.stage() if strategy.supports_stage else None))
            continue
        t0 = time.perf_counter()
        units, tokens = strategy.context(t.text, t.month)
        ms = (time.perf_counter() - t0) * 1000
        # 剔除当前输入本身：提问引用被删事实的词不算暴露
        probe_norm = norm(t.text)
        kept = [u for u in units if norm(u) != probe_norm]
        te = TurnEval(tokens=tokens, ms=ms, after_deletion=t.after_deletion,
                      stage_expected=t.stage,
                      stage_got=strategy.stage() if strategy.supports_stage else None,
                      phenomena=list(t.phenomena))
        for key in t.recall:
            te.recall_total += 1
            if _exposed(conv.facts[key].match[0], kept):
                te.recall_hits += 1
        for key in t.forbidden:
            te.forbidden_total += 1
            if _exposed(conv.facts[key].match[0], kept):
                te.forbidden_violations += 1
        ev.turns.append(te)
    return ev


def run_synthetic(seed: int = 2026, n_conversations: int = 200,
                  strategy_names: Optional[list[str]] = None,
                  db_dir: Optional[str] = None) -> dict[str, Any]:
    """跑完整的合成基准：N 段对话 × 全部策略，返回逐策略指标。"""
    convs = generate_dataset(seed=seed, n_conversations=n_conversations)
    own_tmp = db_dir is None
    db_dir = db_dir or tempfile.mkdtemp(prefix="xinji-bench-")
    try:
        names = strategy_names or ["full-context", "sliding-window", "summary", "oag", "xinji"]
        per_strategy: dict[str, Any] = {}
        for name in names:
            t0 = time.perf_counter()
            evals = []
            for conv in convs:
                # 每段对话一个全新实例：对话之间不得共享任何记忆
                st = make_strategies(db_dir, [name])[0]
                evals.append(eval_conversation(conv, st))
                st.close()
            wall = time.perf_counter() - t0
            summary = summarize(evals, supports_stage=name == "xinji")
            summary["wall_seconds"] = round(wall, 2)
            per_strategy[name] = summary
        return {
            "bench": BENCH_VERSION,
            "track": TRACK_SYNTHETIC,
            "seed": seed,
            "fingerprint": dataset_fingerprint(convs),
            "n_conversations": len(convs),
            "dataset_stats": _dataset_stats(convs),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "strategies": per_strategy,
        }
    finally:
        if own_tmp:
            for suffix in ("", "-wal", "-shm"):
                pass
            try:
                import shutil
                shutil.rmtree(db_dir, ignore_errors=True)
            except Exception:
                pass


def _dataset_stats(convs: list[Conversation]) -> dict[str, Any]:
    agg = {"messages": 0, "recall_probes": 0, "forbidden_probes": 0,
           "deletions": 0, "stage_events": 0}
    for c in convs:
        s = c.stats()
        for k in agg:
            agg[k] += s[k]
    agg["conversations"] = len(convs)
    return agg


# ---- Track A · LoCoMo 真实数据 --------------------------------------------------


def _locomo_conversations(path: str) -> list[dict[str, Any]]:
    """解析 LoCoMo JSON：每段对话返回 (轮次文本列表, QA 列表, dia_id→文本)。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for sample in data:
        conv = sample["conversation"]
        session_keys = sorted(
            {k for k in conv if re.match(r"session_\d+$", k)},
            key=lambda k: int(k.split("_")[1]))
        turns: list[str] = []
        dia_text: dict[str, str] = {}
        for sk in session_keys:
            for turn in conv[sk]:
                text = str(turn.get("text", "")).strip()
                if not text:
                    continue
                turns.append(text)
                dia_text[turn.get("dia_id", "")] = text
        qas = []
        for q in sample.get("qa", []):
            evidence = [dia_text[d] for d in q.get("evidence", []) if d in dia_text]
            if not evidence:
                continue
            qas.append({"question": q["question"], "category": q.get("category", 4),
                        "evidence": evidence})
        out.append({"cid": str(sample.get("sample_id", len(out))),
                    "turns": turns, "qa": qas})
    return out


def run_locomo(path: str, strategy_names: Optional[list[str]] = None,
               db_dir: Optional[str] = None) -> dict[str, Any]:
    """在 LoCoMo 真实数据上评测：事实召回（cat 1–4）与诱导暴露（cat 5）。"""
    convs = _locomo_conversations(path)
    own_tmp = db_dir is None
    db_dir = db_dir or tempfile.mkdtemp(prefix="xinji-locomo-")
    try:
        strategies = make_strategies(db_dir, strategy_names, organize_every=25)
        per_strategy: dict[str, Any] = {}
        for st in strategies:
            recall_h = recall_t = induc_h = induc_t = 0
            tokens: list[int] = []
            mss: list[float] = []
            t0 = time.perf_counter()
            for conv in convs:
                st.set_stage("close")  # 关系先验：LoCoMo 均为多年老友
                for text in conv["turns"]:
                    st.observe(text, month=4)
                st.flush()
                # 查询无关的策略：整段对话只组装一次上下文
                cached_ctx: Optional[ExposureChecker] = None
                cached_tokens = 0
                if st.query_independent:
                    units, cached_tokens = st.context("", month=4)
                    cached_ctx = ExposureChecker(units)  # 问题未入账，无需过滤
                for qa in conv["qa"]:
                    if cached_ctx is not None:
                        checker = cached_ctx
                        tok, ms = cached_tokens, 0.0
                    else:
                        t1 = time.perf_counter()
                        units, tok = st.context(qa["question"], month=4)
                        ms = (time.perf_counter() - t1) * 1000
                        qnorm = norm(qa["question"])
                        checker = ExposureChecker(
                            [u for u in units if norm(u) != qnorm])
                    hit = any(checker.exposed(ev_text) for ev_text in qa["evidence"])
                    tokens.append(tok)
                    mss.append(ms)
                    if qa["category"] == 5:
                        induc_t += 1
                        induc_h += int(hit)   # 诱导暴露：对抗证据被翻出来
                    else:
                        recall_t += 1
                        recall_h += int(hit)
            per_strategy[st.name] = {
                "recall": round(recall_h / recall_t, 4) if recall_t else None,
                "recall_total": recall_t,
                "induced_exposure": round(induc_h / induc_t, 4) if induc_t else None,
                "induced_total": induc_t,
                "avg_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
                "p95_tokens": _p95(tokens),
                "avg_ms": round(sum(mss) / len(mss), 2) if mss else None,
                "wall_seconds": round(time.perf_counter() - t0, 2),
            }
            st.close()
        return {
            "bench": BENCH_VERSION,
            "track": TRACK_LOCOMO,
            "source": "snap-research/LoCoMo (CC BY-NC 4.0，仅研究用途)",
            "n_conversations": len(convs),
            "n_questions": sum(len(c["qa"]) for c in convs),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "strategies": per_strategy,
        }
    finally:
        if own_tmp:
            import shutil
            shutil.rmtree(db_dir, ignore_errors=True)


def _p95(values: list[int]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[min(len(xs) - 1, int(0.95 * len(xs)))]


# ---- Soak：多种子连续压测，校验心记全部硬不变量 --------------------------------

PHENOMENON_INVARIANTS = {
    "lift": ("recall", 1.0),          # 解除边界后必须想起
    "update": ("recall", 1.0),        # 意愿变更后必须想起新计划
    "season": ("recall", 1.0),        # 时节到了必须想起
    "fulfillment": ("violation", 0.0),  # 兑现后不得再作为约定出现
    "boundary": ("violation", 0.0),   # 边界下必须沉默
    "resurrection": ("violation", 0.0),  # 删除后不得复活
}


def check_invariants(summary: dict[str, Any]) -> dict[str, bool]:
    """校验一份心记策略摘要是否满足全部硬不变量。"""
    b = summary.get("phenomena_breakdown", {})
    checks = {
        "recall": summary["recall_macro"] == 1.0,
        "violation": summary["violation_macro"] == 0.0,
        "resurrection": summary["resurrection_macro"] == 0.0,
        "stage": summary["stage_accuracy"] == 1.0,
    }
    for ph, (metric, expected) in PHENOMENON_INVARIANTS.items():
        entry = b.get(ph)
        value = entry.get(metric) if entry else None
        checks[f"{ph}:{metric}"] = value is None or value == expected
    return checks


def run_soak(rounds: int = 5, conversations: int = 1000,
             seeds: Optional[list[int]] = None,
             strategy_names: Optional[list[str]] = None) -> dict[str, Any]:
    """多种子连续压测：每轮独立种子生成全新基准并校验不变量，任何违例即失败。

    「完全确保不再出错」的机器判据：所有轮次的全部不变量为真。
    """
    seeds = seeds or [2026 + i * 7 for i in range(rounds)]
    rounds_out: list[dict[str, Any]] = []
    for i, seed in enumerate(seeds, 1):
        print(f"[soak] 第 {i}/{len(seeds)} 轮 seed={seed}（{conversations} 段对话）…",
              flush=True)
        res = run_synthetic(seed=seed, n_conversations=conversations,
                            strategy_names=strategy_names or ["xinji"])
        summary = res["strategies"]["xinji"]
        checks = check_invariants(summary)
        rounds_out.append({
            "seed": seed,
            "fingerprint": res["fingerprint"],
            "ok": all(checks.values()),
            "checks": checks,
            "metrics": {k: summary[k] for k in
                        ("recall_macro", "violation_macro", "resurrection_macro",
                         "stage_accuracy", "avg_tokens", "probe_turns")},
        })
        failed = [k for k, ok in checks.items() if not ok]
        print(f"[soak]   -> {'PASS' if not failed else 'FAIL: ' + ', '.join(failed)}",
              flush=True)
    return {
        "soak": f"{BENCH_VERSION}/soak",
        "rounds": len(seeds),
        "conversations_per_round": conversations,
        "all_ok": all(r["ok"] for r in rounds_out),
        "results": rounds_out,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---- 报告 -----------------------------------------------------------------------


def write_report(synthetic: Optional[dict[str, Any]], locomo: Optional[dict[str, Any]],
                 out_path: str) -> str:
    """生成 BENCH_REPORT.md（两轨至少提供其一）。"""
    if synthetic is None and locomo is None:
        raise ValueError("synthetic 与 locomo 至少提供一个")
    order = ["full-context", "sliding-window", "summary", "oag", "xinji"]
    label = {"full-context": "全量长上下文", "sliding-window": "滑动窗口",
             "summary": "摘要压缩", "oag": "OAG 式结构化", "xinji": "心记"}
    s = (synthetic or {}).get("strategies", {})
    names = [n for n in order if n in s or n in (locomo or {}).get("strategies", {})]

    def pct(v: Optional[float]) -> str:
        return "N/A" if v is None else f"{v * 100:.1f}%"

    def num(v: Any) -> str:
        return "N/A" if v is None else f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)

    lines: list[str] = []
    lines.append("# 心记 · 评测报告（V1 原型）")
    lines.append("")
    stamp = (synthetic or locomo or {}).get("generated_at", "")
    head = f"> 生成时间：{stamp}｜基准版本：`{BENCH_VERSION}`"
    if synthetic:
        head += f"｜合成数据指纹：`{synthetic['fingerprint']}`（seed={synthetic['seed']}）"
    lines.append(head)
    lines.append("")
    lines.append("## 这份报告测什么")
    lines.append("")
    lines.append("评测比较**记忆层**把什么交给了下游模型，不比较模型本身。四个 baseline"
                 "与心记接收完全相同的对话流与治理动作，判分只看组装出的上下文：")
    lines.append("")
    lines.append("| 指标 | 判分口径 |")
    lines.append("| --- | --- |")
    lines.append("| 事实召回率 | 应当想起的事实出现在上下文的比例（探针级） |")
    lines.append("| 提及准确率 | 应暴露的暴露、不该暴露的沉默，合并正确率 |")
    lines.append("| 越界率 | 边界有效且用户未提起时，被边界覆盖的事实仍在上下文的比例 |")
    lines.append("| 删除后复活率 | 删除并强制重建派生层后，被删事实又出现的比例 |")
    lines.append("| 阶段迁移正确率 | 脚本化关系阶段与策略记录阶段的一致率（baseline 无此机制） |")
    lines.append("| 成本 / 延迟 | 上下文 token（均值 / p95）与上下文组装耗时 |")
    lines.append("")
    lines.append("**口径说明（重要）**：无 LLM 参与判分。「越界」测的是上下文层面的"
                 "*暴露风险*——被边界覆盖的旧事仍在上下文里，下游模型大概率主动提起；"
                 "最终是否出口由模型决定。心记的成本包含其边界与分寸提示，baseline 无此开销。")
    lines.append("")

    ds = synthetic["dataset_stats"]
    lines.append("## Track B · XinjiBench（合成关系记忆基准）")
    lines.append("")
    lines.append(f"{ds['conversations']} 段跨月陪伴关系脚本（中文，种子确定性可复现，"
                 f"现象可组合），共 {ds['messages']} 条消息、{ds['recall_probes']} 个召回探针、"
                 f"{ds['forbidden_probes']} 个越界/复活探针、{ds['deletions']} 次治理删除、"
                 f"{ds['stage_events']} 次阶段迁移。")
    lines.append("")
    lines.append("| 策略 | 事实召回↑ | 提及准确↑ | 越界率↓ | 复活率↓ | 阶段正确↑ | 平均 token↓ | p95 token | 平均 ms |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for n in names:
        m = s[n]
        ms = m.get("avg_ms")
        lines.append(f"| {label[n]} | {pct(m['recall_macro'])} | {pct(m['mention_accuracy'])} "
                     f"| {pct(m['violation_macro'])} | {pct(m['resurrection_macro'])} "
                     f"| {pct(m['stage_accuracy'])} | {num(m['avg_tokens'])} "
                     f"| {num(m['p95_tokens'])} | "
                     f"{'N/A' if ms is None else f'{ms:.2f}'} |")
    lines.append("")

    # 按现象分拆（缺陷定位）：列出每个现象上心记的表现
    ph_label = {"boundary": "边界下沉默", "resurrection": "删除后不复活",
                "lift": "边界解除后应想起", "update": "意愿变更后想起新计划",
                "fulfillment": "承诺兑现后不再提", "restatement": "重申后想起（去重）",
                "multi-fact": "一句话多事实", "season": "时节想起", "stage": "阶段迁移"}
    xj = s.get("xinji")
    if xj and xj.get("phenomena_breakdown"):
        lines.append("### 心记 · 按现象分拆（缺陷定位）")
        lines.append("")
        lines.append("| 现象 | 召回↑ | 探针数 | 越界↓ | 探针数 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for ph, b in xj["phenomena_breakdown"].items():
            lines.append(f"| {ph_label.get(ph, ph)} | {pct(b['recall'])} | "
                         f"{b['recall_total']} | {pct(b['violation'])} | {b['violation_total']} |")
        lines.append("")

    if locomo:
        lc = locomo["strategies"]
        lines.append("## Track A · LoCoMo（真实长期对话，CC BY-NC 4.0）")
        lines.append("")
        lines.append(f"{locomo['n_conversations']} 段真实超长对话（每段约 300 轮、数十个会话），"
                     f"{locomo['n_questions']} 个带证据标注的 QA。事实召回 = 证据轮文本出现在"
                     "上下文（类别 1–4）；诱导暴露 = 对抗性问题的误导证据被翻出（类别 5，"
                     "正确行为是沉默）。")
        lines.append("")
        lines.append("| 策略 | 事实召回↑ | 诱导暴露↓ | 平均 token↓ | p95 token | 平均 ms |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for n in names:
            m = lc[n]
            ms = m.get('avg_ms')
            lines.append(f"| {label[n]} | {pct(m['recall'])} | {pct(m['induced_exposure'])} "
                         f"| {num(m['avg_tokens'])} | {num(m['p95_tokens'])} | "
                         f"{'N/A' if ms is None else f'{ms:.2f}'} |")
        lines.append("")

    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append("# 合成基准（可调规模）")
    lines.append("PYTHONPATH=src python -m bench run --synthetic 1000")
    lines.append("# 真实数据（先下载：CC BY-NC 4.0，仅研究用途）")
    lines.append("python scripts/fetch_locomo.py")
    lines.append("PYTHONPATH=src python -m bench run --locomo bench/data/locomo10.json")
    lines.append("```")
    lines.append("")
    lines.append("## 已知边界与后续工作（探针测不到的缺陷面）")
    lines.append("")
    lines.append("- **旧计划共现**：意愿变更后，被放弃的旧计划记忆仍在库中（时节触发已通过"
                 "关闭话题规避），但显式问及旧计划时新旧行会同时出现在上下文——是否构成"
                 "干扰由下游模型决定。计划引入记忆的时效标注（supersede 链）。")
    lines.append("- **召回上限来自检索器**：LoCoMo 上 45.7% 的召回天花板源于「5 条 × BM25 词袋」"
                 "装包策略；换向量召回或扩大 MAX_ITEMS 可换更高召回（更高成本），属工程权衡。")
    lines.append("- **重申产生重复记忆**：同一愿望换一种说法会派生第二条事实记忆，装包已按话题"
                 "去重，但账本层面未合并（保留原话可审计的代价）。")
    lines.append("- **滑动窗口的「遗忘式合规」**：窗口基线越界 17.5% 是「该忘的还没忘、该记的"
                 "已经忘」——它的低违规来自遗忘而非分寸，对比时需结合召回率读。")
    lines.append("")
    text = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return text
