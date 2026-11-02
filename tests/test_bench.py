"""bench 自检：数据生成确定性、语料约束、指标联动的不变量。"""

from __future__ import annotations

import unittest

from bench.baselines import make_strategies
from bench.runner import eval_conversation, run_synthetic
from bench.scenarios import generate_dataset, dataset_fingerprint


class DatasetTest(unittest.TestCase):
    def test_deterministic(self):
        """同一种子必然生成同一份基准（可复现）。"""
        a = generate_dataset(seed=7, n_conversations=3)
        b = generate_dataset(seed=7, n_conversations=3)
        self.assertEqual(dataset_fingerprint(a), dataset_fingerprint(b))
        c = generate_dataset(seed=8, n_conversations=3)
        self.assertNotEqual(dataset_fingerprint(a), dataset_fingerprint(c))

    def test_probe_coverage(self):
        """每段对话覆盖全部探针类型（召回 / 越界或解除 / 复活 / 阶段 / 删除）。"""
        for conv in generate_dataset(seed=1, n_conversations=8):
            s = conv.stats()
            self.assertGreaterEqual(s["recall_probes"], 3)
            self.assertGreaterEqual(s["forbidden_probes"], 2)
            self.assertGreaterEqual(s["deletions"], 2)
            self.assertGreaterEqual(s["stage_events"], 2)

    def test_phenomena_mix(self):
        """现象抽样在足够多的对话里铺开（每类现象都有出现）。"""
        convs = generate_dataset(seed=2, n_conversations=40)
        seen = set()
        for c in convs:
            seen.update(c.phenomena)
        self.assertTrue({"lift", "update", "fulfillment"} <= seen, seen)
        # lift 与 boundary 互补：有 lift 的对话，秋天时节探针变成召回
        for c in convs:
            autumn_probe = next(t for t in c.turns
                                if t.month == 10 and (t.recall or t.forbidden))
            if "lift" in c.phenomena:
                self.assertIn("wish_a", autumn_probe.recall)
            else:
                self.assertIn("wish_a", autumn_probe.forbidden)


class MetricWiringTest(unittest.TestCase):
    """用 12 段对话做全链路小跑，验证指标与系统承诺联动。"""

    @classmethod
    def setUpClass(cls):
        cls.results = run_synthetic(seed=7, n_conversations=12)

    def test_xinji_invariants(self):
        """心记的硬承诺：越界率 0、复活率 0、阶段迁移全对。"""
        m = self.results["strategies"]["xinji"]
        self.assertEqual(m["violation_macro"], 0.0, m)
        self.assertEqual(m["resurrection_macro"], 0.0, m)
        self.assertEqual(m["stage_accuracy"], 1.0, m)

    def test_xinji_phenomena(self):
        """新现象在心记上必须闭环：解除后想起、变更后想新、兑现后沉默。"""
        m = self.results["strategies"]["xinji"]
        b = m["phenomena_breakdown"]
        if b.get("lift", {}).get("recall_total"):
            self.assertEqual(b["lift"]["recall"], 1.0, b)
        if b.get("update", {}).get("recall_total"):
            self.assertEqual(b["update"]["recall"], 1.0, b)
        if b.get("fulfillment", {}).get("violation_total"):
            self.assertEqual(b["fulfillment"]["violation"], 0.0, b)

    def test_fullcontext_resurrects_and_violates(self):
        """全量基线：删除无效（复活 100%）、越界 100%——正是心记要解决的问题。"""
        m = self.results["strategies"]["full-context"]
        self.assertEqual(m["resurrection_macro"], 1.0, m)
        self.assertEqual(m["violation_macro"], 1.0, m)

    def test_summary_resurrects(self):
        """摘要基线重建摘要时被删内容随之复活。"""
        m = self.results["strategies"]["summary"]
        self.assertEqual(m["resurrection_macro"], 1.0, m)

    def test_baseline_no_stage(self):
        """baseline 无关系阶段机制：阶段正确率记 N/A。"""
        for name in ("full-context", "sliding-window", "summary", "oag"):
            self.assertIsNone(self.results["strategies"][name]["stage_accuracy"])


class ProbeCountTest(unittest.TestCase):
    def test_probe_counts_match(self):
        """探针数与数据集标注一致（判分没漏）。"""
        results = run_synthetic(seed=7, n_conversations=3, strategy_names=["xinji"])
        ds = results["dataset_stats"]
        self.assertEqual(ds["conversations"], 3)
        m = results["strategies"]["xinji"]
        self.assertGreater(m["probe_turns"], 0)


class StrategyInterfaceTest(unittest.TestCase):
    def test_all_strategies_run(self):
        """五个策略都能完整跑完一段对话且返回合法指标。"""
        conv = generate_dataset(seed=3, n_conversations=1)[0]
        import tempfile
        db_dir = tempfile.mkdtemp(prefix="xj-test-")
        try:
            for st in make_strategies(db_dir):
                ev = eval_conversation(conv, st)
                self.assertGreater(len(ev.turns), 0)
                st.close()
        finally:
            import shutil
            shutil.rmtree(db_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
