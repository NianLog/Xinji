"""忘得掉：删除真正生效；补整理、重放历史，都不能让旧内容复活。"""

from __future__ import annotations

import unittest

from .helpers import AUTUMN, autumn_sea_fixture, cleanup, make_engine


class DeleteMemoryTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)
        autumn_sea_fixture(self.eng)
        self.sea = [m for m in self.eng.view()["memories"] if m["topic"] == "看海"]
        self.assertGreaterEqual(len(self.sea), 1)

    def test_memory_gone_after_delete(self):
        mem = self.sea[0]
        self.eng.delete_memory(mem["id"], reason="不想要了")
        pack = self.eng.read("对了，我决定十月去看海！", now=AUTUMN)
        ids = [m["id"] for m in pack["memories"]]
        self.assertNotIn(mem["id"], ids)

    def test_no_resurrect_on_reorganize(self):
        """补整理不能让已删除记忆复活。"""
        mem = self.sea[0]
        self.eng.delete_memory(mem["id"])
        self.eng.organize()
        self.eng.store.set_meta("organized_upto", "0")  # 强制全量补整理
        self.eng.organize()
        self.assertIsNone(self.eng.store.memory(mem["id"]))

    def test_no_resurrect_on_replay(self):
        """重放整本账本，已删除记忆不复活，且状态与现场一致。"""
        mem = self.sea[0]
        self.eng.delete_memory(mem["id"])
        self.eng.organize()
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])

    def test_no_resurrect_on_rebuild(self):
        """清空派生层后原地重建，已删除记忆同样不回来。"""
        mem = self.sea[0]
        self.eng.delete_memory(mem["id"])
        self.eng.rebuild()
        self.assertIsNone(self.eng.store.memory(mem["id"]))
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])


class DeleteMessageTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_delete_message_drops_derivations(self):
        """删除原话后，从它派生的全部记忆撤下，重放一致。"""
        ack = self.eng.hear("我叫小满。")
        self.eng.organize()
        self.assertTrue(any(m["kind"] == "profile" for m in self.eng.view()["memories"]))
        self.eng.delete_messages([ack["seq"]], reason="说错了")
        self.assertFalse(any(m["kind"] == "profile" for m in self.eng.view()["memories"]))
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])
        # 原话不可变：事件还在，但所有读取路径都跳过它
        self.assertIsNone(self.eng.store.event(ack["seq"]).payload.get("deleted"))
        self.assertNotIn("她叫小满", self.eng.profile_summary())
        recent = [t["text"] for t in self.eng.recent_turns()]
        self.assertNotIn("我叫小满。", recent)

    def test_safe_export_redacts_deleted(self):
        """安全导出：已删除原话以墓碑占位；完整导出保留原件。"""
        ack = self.eng.hear("一个秘密。")
        self.eng.organize()
        self.eng.delete_messages([ack["seq"]])
        safe = self.eng.export()
        texts = [e["payload"].get("text") for e in safe["events"] if e["type"] == "message"]
        self.assertIn("［已删除］", texts)
        self.assertNotIn("一个秘密。", texts)
        full = self.eng.export(full=True)
        full_texts = [e["payload"].get("text") for e in full["events"] if e["type"] == "message"]
        self.assertIn("一个秘密。", full_texts)  # 完整导出用于验证账本，原件必须留


class DeleteTopicTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_delete_topic_closes_open_topic(self):
        autumn_sea_fixture(self.eng)
        result = self.eng.delete_topic("看海", reason="这段不想留")
        self.assertGreaterEqual(len(result["dropped_memories"]), 1)
        open_topics = [t for t in self.eng.state()["open_topics"] if t["status"] == "open"]
        self.assertEqual(open_topics, [])
        pack = self.eng.read("对了，我决定十月去看海！", now=AUTUMN)
        self.assertEqual([m for m in pack["memories"] if m["topic"] == "看海"], [])
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])


class CorrectionTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_correction_visible_and_replayable(self):
        """纠正改变当前内容，旧内容留在账本可审计，重放一致。"""
        self.eng.hear("我喜欢喝热奶茶。")
        self.eng.organize()
        fact = next(m for m in self.eng.view()["memories"] if m["kind"] == "fact")
        self.eng.correct_memory(fact["id"], "我现在更喜欢喝茶了", reason="口味变了")
        corrected = self.eng.store.memory(fact["id"])
        self.assertEqual(corrected["content"], "我现在更喜欢喝茶了")
        self.assertTrue(corrected["corrected"])
        self.assertIn("我现在更喜欢喝茶了", self.eng.profile_summary())
        audit = self.eng.audit(type_="memory.correction")
        self.assertEqual(len(audit), 1)
        result = self.eng.replay()
        self.assertTrue(result["ok"], result["differences"])
        self.assertEqual(self.eng.store.memory(fact["id"])["content"],
                         "我现在更喜欢喝茶了")


if __name__ == "__main__":
    unittest.main()
