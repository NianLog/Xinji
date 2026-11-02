"""L0 账本：原话不可变、追加式、哈希链可验证。"""

from __future__ import annotations

import unittest

from xinji.events import DELETION, MESSAGE, EventBuilder

from .helpers import cleanup, make_engine


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.eng, self.path = make_engine()
        self.addCleanup(cleanup, self.eng, self.path)

    def test_append_only_and_chain(self):
        """账本只追加：写入后事件永久在册，哈希链完好。"""
        self.eng.hear("第一句。")
        self.eng.hear("第二句。", role="assistant")
        self.eng.hear("第三句。")
        events = list(self.eng.store.events())
        self.assertEqual([e.seq for e in events], [1, 2, 3])
        self.assertEqual(events[0].prev_hash, "0" * 64)
        self.assertEqual(events[1].prev_hash, events[0].hash)
        self.assertEqual(self.eng.verify_ledger(), [])

    def test_tamper_detected(self):
        """外部篡改载荷会被哈希链发现。"""
        self.eng.hear("一句真话。")
        with self.eng.store.tx() as conn:
            conn.execute("UPDATE ledger SET payload = ? WHERE seq = 1",
                         ('{"role":"user","text":"一句被篡改的话。","turn":1}',))
        broken = self.eng.verify_ledger()
        self.assertEqual(broken, [1])

    def test_message_never_rewritten(self):
        """删除只是追加墓碑事件，原消息事件原封不动。"""
        ack = self.eng.hear("想被删掉的话。")
        self.eng.delete_messages([ack["seq"]])
        original = self.eng.store.event(ack["seq"])
        self.assertIsNotNone(original)
        self.assertEqual(original.type, MESSAGE)
        self.assertEqual(original.payload["text"], "想被删掉的话。")
        kinds = [e.type for e in self.eng.store.events()]
        self.assertIn(DELETION, kinds)

    def test_append_many_atomic_order(self):
        builders = [EventBuilder(MESSAGE, {"role": "user", "text": f"批量{i}", "turn": i})
                    for i in range(3)]
        events = self.eng.store.append_many(builders)
        self.assertEqual([e.seq for e in events], [1, 2, 3])
        self.assertEqual(self.eng.verify_ledger(), [])


if __name__ == "__main__":
    unittest.main()
