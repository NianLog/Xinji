"""写路径与读路径：先记下来、想好再说、预算与条数约束。"""

from __future__ import annotations

import unittest

from xinji import judgment

from .helpers import AUTUMN, SPRING, autumn_sea_fixture, cleanup, make_engine


class WritePathTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_write_ack_immediate(self):
        """原话立刻进 L0 并确认；整理不阻塞聊天。"""
        ack = self.eng.hear("我想在秋天去看海。")
        self.assertGreater(ack["seq"], 0)
        self.assertEqual(ack["turn"], 1)
        self.assertEqual(ack["pending"], 1)  # 尚未整理
        pack = self.eng.read("什么都行", now=SPRING)  # 不整理也能继续读
        self.assertEqual(pack["mode"], "normal")

    def test_organize_derives_l1_and_l2(self):
        """整理派生事实记忆与关系变更（开放话题 + 边界）。"""
        autumn_sea_fixture(self.eng)
        view = self.eng.view()
        facts = [m for m in view["memories"] if m["kind"] == "fact"]
        self.assertTrue(any("看海" in m["content"] for m in facts))
        self.assertEqual(len(view["open_topics"]), 1)
        self.assertEqual(view["open_topics"][0]["topic"], "看海")
        self.assertEqual(len(view["boundaries"]), 1)
        self.assertEqual(view["boundaries"][0]["topic"], "看海")
        # 证据链：边界指向她说过「别催我」那句话的 seq（第 3 号事件）
        self.assertEqual(view["boundaries"][0]["evidence"], [3])

    def test_organize_idempotent(self):
        """重复整理不产生重复记忆或重复关系变更。"""
        autumn_sea_fixture(self.eng)
        n_before = len(self.eng.view()["memories"])
        v_before = self.eng.view()["relationship_version"]
        report = self.eng.organize()
        self.assertEqual(report["processed"], 0)
        self.assertEqual(len(self.eng.view()["memories"]), n_before)
        self.assertEqual(self.eng.view()["relationship_version"], v_before)

    def test_session_summary_bucket(self):
        """凑满一桶消息后生成确定性片段摘要。"""
        for i in range(8):
            self.eng.hear(f"第{i}句日常。")
        self.eng.organize()
        summaries = [m for m in self.eng.view()["memories"] if m["kind"] == "summary"]
        self.assertEqual(len(summaries), 1)
        replay = self.eng.replay()
        self.assertTrue(replay["ok"], replay["differences"])


class ReadPathTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_pack_budget_and_item_limits(self):
        """读路径最多 5 条、总 token 不超过 8k 预算。"""
        # 塞进远超预算的记忆
        for i in range(20):
            self.eng.store.upsert_memory({
                "id": f"mem{i}", "kind": "episode", "topic": f"话题{i}",
                "content": f"关于话题{i}的很长很长的一段回忆" * 50,
                "content_hash": f"hash{i}", "provenance": [i + 1],
                "min_stage": 0, "confidence": 0.9,
                "tokens": judgment.TOKEN_BUDGET // 3, "derived_at": i + 1,
            })
        pack = self.eng.read("话题1 话题2 话题3 话题4 话题5 话题6", now=AUTUMN)
        self.assertLessEqual(len(pack["memories"]), judgment.MAX_ITEMS)
        self.assertLessEqual(pack["budget"]["used"], judgment.TOKEN_BUDGET)

    def test_recent_turns_and_profile_in_pack(self):
        """正常包也带最近几轮与画像摘要。"""
        self.eng.hear("我叫小满。")
        self.eng.organize()
        self.eng.hear("我喜欢喝热奶茶。")
        self.eng.organize()
        pack = self.eng.read("今天怎么样", now=AUTUMN)
        self.assertGreaterEqual(len(pack["recent_turns"]), 2)
        self.assertIn("她叫小满", pack["profile_summary"])

    def test_recently_mentioned_not_repeated(self):
        """刚提过的记忆短期内不再主动重复（不唠叨）。"""
        self.eng.hear("我喜欢吃桂花糕。")
        self.eng.organize()
        self.eng.hear("桂花糕还有吗")  # 新的一轮，轮内刚说的话不算「旧事」
        p1 = self.eng.read("桂花糕还有吗", now=AUTUMN)
        self.assertGreaterEqual(len(p1["memories"]), 1)
        self.eng.hear("还想吃桂花糕")  # 再来一轮
        p2 = self.eng.read("还想吃桂花糕", now=AUTUMN)
        reasons = {s["judgment"] for s in p2["suppressed"]}
        self.assertIn("recently-mentioned", reasons)
        ids1 = {m["id"] for m in p1["memories"]}
        ids2 = {m["id"] for m in p2["memories"]}
        self.assertFalse(ids1 & ids2)

    def test_current_turn_not_packed_back(self):
        """本轮刚说的话不会作为「记忆」再装回包里。"""
        self.eng.hear("我喜欢吃桂花糕。")
        self.eng.organize()
        pack = self.eng.read("我喜欢吃桂花糕。", now=AUTUMN)  # 同一轮复读
        self.assertEqual(pack["memories"], [])

    def test_open_commitments_in_pack(self):
        """「记得住：约定」——开放承诺随包携带，兑现后不再出现。"""
        self.eng.hear("说好了下次带桂花糕给你。")
        self.eng.organize()
        pack = self.eng.read("最近怎么样", now=AUTUMN)
        self.assertEqual([c["what"] for c in pack["commitments"]],
                         ["下次带桂花糕给你"])
        self.eng.relationship_op("fulfill_commitment", {"ref": "下次带桂花糕给你"})
        pack = self.eng.read("最近怎么样", now=AUTUMN)
        self.assertEqual(pack["commitments"], [])


if __name__ == "__main__":
    unittest.main()
