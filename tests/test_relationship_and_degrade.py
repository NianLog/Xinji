"""关系版本化与回滚、先降级不装记得、一键停用。"""

from __future__ import annotations

import unittest
from unittest import mock

from .helpers import AUTUMN, SPRING, autumn_sea_fixture, cleanup, make_engine


class RelationshipVersioningTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)
        autumn_sea_fixture(self.eng)

    def test_every_change_logged_with_evidence(self):
        log = self.eng.store.relationship_log()
        self.assertGreaterEqual(len(log), 2)  # open_topic + set_boundary
        boundary_rows = [r for r in log if r["op"] == "set_boundary"]
        self.assertEqual(len(boundary_rows), 1)
        self.assertIsNone(boundary_rows[0]["before"])   # 新增前为空
        self.assertEqual(boundary_rows[0]["after"]["topic"], "看海")

    def test_rollback_restores_previous_version(self):
        """不用一个分数代表关系；每次变化可回滚，回滚本身也留底。"""
        v_before = self.eng.view()["relationship_version"]
        self.eng.relationship_op("lift_boundary", {"ref": "看海"}, reason="暂准聊")
        self.assertEqual(len(self.eng.hard_boundaries()), 0)
        self.eng.rollback_relationship(v_before)
        self.assertEqual(len(self.eng.hard_boundaries()), 1)  # 边界回来了
        log = self.eng.store.relationship_log()
        self.assertEqual(log[-1]["op"], "restore")
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])

    def test_stage_changes_tracked(self):
        self.eng.set_stage("close")
        st = self.eng.state()
        self.assertEqual(st["stage"]["value"], "close")
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])


class DegradeTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)
        autumn_sea_fixture(self.eng)
        self.eng.hear("我叫小满。")
        self.eng.organize()

    def test_disable_degrades_read(self):
        """一键停用：只带当前输入、最近几轮、画像摘要与硬边界。"""
        self.eng.disable()
        pack = self.eng.read("今天好累", now=AUTUMN)
        self.assertEqual(pack["mode"], "disabled")
        self.assertEqual(pack["memories"], [])
        self.assertGreaterEqual(len(pack["recent_turns"]), 1)
        self.assertIn("她叫小满", pack["profile_summary"])  # 有效画像摘要仍在
        self.assertEqual(len(pack["hard_boundaries"]), 1)   # 当前硬边界仍在
        # 聊天照常：停用期间照样记账
        ack = self.eng.hear("先这样吧。")
        self.assertGreater(ack["seq"], 0)

    def test_enable_resumes(self):
        self.eng.disable()
        self.eng.enable()
        pack = self.eng.read("对了，十月去看海！", now=AUTUMN)
        self.assertEqual(pack["mode"], "normal")
        self.assertGreaterEqual(len(pack["memories"]), 1)
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])

    def test_read_failure_auto_degrades(self):
        """读路径故障自动降级：不装记得，聊天继续。"""
        with mock.patch("xinji.retrieval.Retriever.query", side_effect=RuntimeError("索引炸了")):
            pack = self.eng.read("随便聊聊", now=AUTUMN)
        self.assertEqual(pack["mode"], "degraded")
        self.assertIsNotNone(pack["note"])
        self.assertIn("故障", pack["note"])
        # 降级状态持续，直到恢复
        self.assertEqual(self.eng.mode(), "degraded")
        pack2 = self.eng.read("再聊聊", now=AUTUMN)
        self.assertEqual(pack2["mode"], "degraded")
        # 恢复：回到正常并补整理
        self.eng.hear("恢复前说的一句话。")
        result = self.eng.recover()
        self.assertEqual(self.eng.mode(), "active")
        self.assertGreaterEqual(result["organize"]["processed"], 1)
        pack3 = self.eng.read("对了，十月去看海！", now=AUTUMN)
        self.assertEqual(pack3["mode"], "normal")
        replay = self.eng.replay()
        self.assertTrue(replay["ok"], replay["differences"])

    def test_organize_failure_degrades(self):
        """整理故障同样先降级，不阻塞聊天。"""
        self.eng.hear("等下再整理的这句话。")  # 制造积压
        with mock.patch("xinji.engine.extract", side_effect=RuntimeError("整理器炸了")):
            report = self.eng.organize()
        self.assertIsNotNone(report["error"])
        self.assertEqual(self.eng.mode(), "degraded")
        pack = self.eng.read("今晚吃什么", now=SPRING)
        self.assertEqual(pack["mode"], "degraded")
        self.assertEqual(pack["memories"], [])  # 不装记得


if __name__ == "__main__":
    unittest.main()
