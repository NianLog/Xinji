"""说得对：边界先于相似度、默认沉默、阶段门、承诺与分寸。"""

from __future__ import annotations

import unittest

from .helpers import AUTUMN, SPRING, WINTER, autumn_sea_fixture, cleanup, make_engine


class BoundaryBeforeSimilarityTest(unittest.TestCase):
    """README 场景：秋天到了，她没提起海，心记保持安静。"""

    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)
        autumn_sea_fixture(self.eng)

    def test_proactive_blocked_by_boundary(self):
        """时节触发想起看海，但「别催我」有效 → 默认沉默。"""
        pack = self.eng.read("最近降温了，有点想喝热奶茶。", now=AUTUMN)
        self.assertEqual(pack["mode"], "normal")
        self.assertEqual(pack["memories"], [])  # 空包：默认沉默
        suppressed = [s for s in pack["suppressed"]
                      if s["judgment"] == "boundary-proactive"]
        self.assertGreaterEqual(len(suppressed), 1)
        self.assertIn("默认沉默", pack["guidance"])

    def test_user_initiated_allowed_with_directive(self):
        """她自己提起海：允许接上，且包里带「分寸」提示。"""
        pack = self.eng.read("对了，我决定十月去看海！", now=AUTUMN)
        topics = [m["topic"] for m in pack["memories"]]
        self.assertIn("看海", topics)
        first = pack["memories"][0]
        self.assertEqual(first["judgment"], "allowed-user-initiated")
        self.assertIn("directive", first)
        self.assertIn("别催", pack["guidance"] + first["directive"]["text"])

    def test_boundary_lifted_then_proactive_ok(self):
        """边界解除后，同一场景允许主动自然接上。"""
        self.eng.relationship_op("lift_boundary", {"ref": "看海"}, reason="她说可以聊了")
        pack = self.eng.read("最近降温了，有点想喝热奶茶。", now=AUTUMN)
        topics = [m["topic"] for m in pack["memories"]]
        self.assertIn("看海", topics)
        self.assertNotIn("boundary-proactive",
                         {s["judgment"] for s in pack["suppressed"]})

    def test_wrong_season_no_proactive(self):
        """冬天没到约定时节：连候选都不产生。"""
        pack = self.eng.read("最近降温了，有点想喝热奶茶。", now=WINTER)
        self.assertEqual(pack["memories"], [])
        reasons = {s["judgment"] for s in pack["suppressed"]}
        self.assertNotIn("boundary-proactive", reasons)


class DefaultSilenceTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_no_evidence_no_mention(self):
        """毫无交集的输入：不硬凑记忆，默认沉默。"""
        self.eng.hear("我想在秋天去看海。")
        self.eng.organize()
        pack = self.eng.read("今晚吃什么好呢", now=SPRING)
        self.assertEqual(pack["memories"], [])
        self.assertEqual(pack["suppressed"], [])
        self.assertIn("默认沉默", pack["guidance"])

    def test_low_confidence_blocked(self):
        """证据不足的记忆不出口。"""
        self.eng.store.upsert_memory({
            "id": "weak", "kind": "fact", "topic": "猜测",
            "content": "她好像也许大概喜欢蓝色",
            "content_hash": "weakhash", "provenance": [1],
            "min_stage": 0, "confidence": 0.1, "tokens": 10, "derived_at": 1,
        })
        pack = self.eng.read("她喜欢什么颜色", now=SPRING)
        self.assertEqual(pack["memories"], [])
        reasons = {s["judgment"] for s in pack["suppressed"]}
        self.assertIn("low-confidence", reasons)


class StageGateTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_story_needs_closer_stage(self):
        """共同故事至少要「熟悉」；初识阶段即使高度相关，故事也不出口。"""
        self.eng.hear("还记得我们上次一起修好了那盏台灯吗？")
        self.eng.organize()
        self.eng.hear("先聊点别的。")  # 进入新一轮，旧故事才可能被引用
        self.eng.organize()
        pack = self.eng.read("我们上次一起修的台灯还好吗", now=SPRING)
        # 情景（原话回声）可以随之出现，但「共同故事」被阶段门挡下
        self.assertFalse([m for m in pack["memories"] if m["kind"] == "story"])
        self.assertIn("stage-below", {s["judgment"] for s in pack["suppressed"]})

        self.eng.set_stage("building")
        self.eng.hear("再聊聊台灯。")  # 又一轮
        pack = self.eng.read("我们上次一起修的台灯还好吗", now=SPRING)
        stories = [m for m in pack["memories"] if m["kind"] == "story"]
        self.assertGreaterEqual(len(stories), 1)


class CommitmentTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_commitment_lifecycle_versioned(self):
        ev = self.eng.relationship_op("add_commitment", {"what": "生日时送她一本书"})
        st = self.eng.state()
        self.assertEqual(len(st["commitments"]), 1)
        self.assertEqual(st["commitments"][0]["status"], "open")
        self.eng.relationship_op("fulfill_commitment", {"ref": "生日时送她一本书"})
        st = self.eng.state()
        self.assertEqual(st["commitments"][0]["status"], "fulfilled")
        log = self.eng.store.relationship_log()
        self.assertEqual(len(log), 2)  # 每次变更都留底
        self.assertIsNone(log[0]["before"])        # 新增承诺：之前没有
        self.assertIsNotNone(log[1]["before"])     # 兑现：之前是 open


if __name__ == "__main__":
    unittest.main()
