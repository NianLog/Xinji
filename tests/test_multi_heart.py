"""多心（multi-heart）服务的安全验收：QQ 等多人场景的底线。

一段关系一颗心：A 的记忆绝不能出现在 B 的读取里；
heart id 是路径穿越的唯一防线；LRU 池保证长期运行不泄漏。
"""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime

from xinji.server import HeartError, HeartRouter, serve

AUTUMN = datetime(2026, 10, 15)


class _Client:
    def __init__(self, port: int):
        self.port = port

    def _req(self, method: str, path: str, body=None, heart=None):
        headers = {"Content-Type": "application/json"}
        if heart is not None:
            headers["X-Xinji-Heart"] = heart
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path,
                     json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                     headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def get(self, path, heart=None):
        return self._req("GET", path, heart=heart)

    def post(self, path, body=None, heart=None):
        return self._req("POST", path, body=body, heart=heart)


class MultiHeartServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hearts_dir = tempfile.mkdtemp(prefix="xj-hearts-")
        cls.port = 8900 + os.getpid() % 100
        cls.thread = threading.Thread(
            target=serve, args=(None, "127.0.0.1", cls.port),
            kwargs={"hearts_dir": cls.hearts_dir}, daemon=True)
        cls.thread.start()
        cls.client = _Client(cls.port)
        for _ in range(100):
            try:
                if cls.client.get("/api/v1/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("xinji serve 未就绪")

    # ---- 核心安全承诺 -------------------------------------------------------

    def test_two_hearts_isolated(self):
        """A 与 B 各自许愿：A 的记忆绝不能出现在 B 的读取里。"""
        status, _ = self.client.post("/api/v1/hear",
                                     {"text": "我想在秋天去青岛看海。"}, heart="alice")
        self.assertEqual(status, 200)
        status, _ = self.client.post("/api/v1/hear",
                                     {"text": "我最讨厌香菜，别给我加。"}, heart="bob")
        self.assertEqual(status, 200)

        _, out_a = self.client.post("/api/v1/read",
                                    {"input": "最近降温了。", "now": AUTUMN.isoformat()},
                                    heart="alice")
        _, out_b = self.client.post("/api/v1/read",
                                    {"input": "最近降温了。", "now": AUTUMN.isoformat()},
                                    heart="bob")
        texts_a = json.dumps(out_a["pack"], ensure_ascii=False)
        texts_b = json.dumps(out_b["pack"], ensure_ascii=False)
        self.assertIn("青岛", texts_a, "alice 的心应包含她的愿望")
        self.assertNotIn("香菜", texts_a, "bob 的记忆泄漏进了 alice 的心！")
        self.assertIn("香菜", texts_b, "bob 的心应包含他的偏好")
        self.assertNotIn("青岛", texts_b, "alice 的记忆泄漏进了 bob 的心！")

    def test_boundary_is_per_heart(self):
        """边界只属于一颗心：A 的「别催我」不能挡住 B 的对话。"""
        self.client.post("/api/v1/hear", {"text": "我想在秋天去看海。"}, heart="alice")
        self.client.post("/api/v1/hear", {"text": "到时候别催我订计划。"}, heart="alice")
        _, out_b = self.client.post("/api/v1/read",
                                    {"input": "我决定十月去看海！", "now": AUTUMN.isoformat()},
                                    heart="bob")
        # bob 的心里没有「看海」记忆也没有边界 → 空包但非边界原因
        self.assertEqual(out_b["pack"]["memories"], [])
        self.assertNotIn("boundary-proactive",
                         {s["judgment"] for s in out_b["pack"]["suppressed"]})

    def test_heart_id_validation(self):
        """路径穿越与非法 id 一律 400。"""
        for bad in ("../evil", "a/b", "..", "", "x" * 65, "heart$1"):
            status, err = self.client.post("/api/v1/hear", {"text": "hi"}, heart=bad)
            self.assertEqual(status, 400, f"heart={bad!r} 应被拒绝")
            status, _ = self.client.get("/api/v1/view", heart=bad)
            self.assertEqual(status, 400, f"heart={bad!r} 应被拒绝（GET）")

    def test_hearts_listing_and_backup(self):
        _, hearts = self.client.get("/api/v1/hearts")
        self.assertIn("alice", hearts["hearts"])
        self.assertIn("bob", hearts["hearts"])
        status, backup = self.client.post("/api/v1/backup", {"heart": "alice"})
        self.assertEqual(status, 200)
        self.assertTrue(os.path.exists(backup["backup"]))
        with open(backup["backup"], encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["format"], "xinji.export/v1")

    def test_single_mode_compat(self):
        """单心路由器：任何 heart id 都路由到唯一的心（向后兼容）。"""
        router = HeartRouter(db_path=tempfile.mkstemp(suffix=".db")[1])
        try:
            self.assertIs(router.resolve("alice"), router.resolve("bob"))
        finally:
            router.close_all()

    def test_lru_pool_eviction(self):
        """LRU 池：超过上限时关闭最久未用的心，且不驱逐在途调用。"""
        router = HeartRouter(hearts_dir=self.hearts_dir, max_open=2)
        try:
            h1 = router.resolve("h1")
            h2 = router.resolve("h2")
            h2.call("mode")  # h2 刚用过 → h1 最久未用
            h3 = router.resolve("h3")
            ids = {p.heart_id for p in router._open.values()}
            self.assertNotIn("h1", ids, "最久未用的心应被驱逐")
            self.assertEqual(ids, {"h2", "h3"})
            # 在途保护：h2 的锁被持有（模拟请求执行中）时不会被驱逐
            with h2.lock:
                h4 = router.resolve("h4")  # 需要驱逐一个：跳过在途的 h2 → 驱逐 h3
                ids = {p.heart_id for p in router._open.values()}
                self.assertIn("h2", ids, "在途调用的心不应被驱逐")
                self.assertEqual(ids, {"h2", "h4"})
            h2.call("mode")  # h2 恢复空闲并刷新使用时间
            h5 = router.resolve("h5")  # 此时 h4 最久未用 → 驱逐 h4
            ids = {p.heart_id for p in router._open.values()}
            self.assertEqual(ids, {"h2", "h5"})
        finally:
            router.close_all()

    def test_router_rejects_bad_id_directly(self):
        router = HeartRouter(hearts_dir=self.hearts_dir)
        with self.assertRaises(HeartError):
            router.normalize("../evil")


if __name__ == "__main__":
    unittest.main()
