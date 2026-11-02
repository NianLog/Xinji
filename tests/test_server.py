"""xinji serve：开放接入 HTTP API 的端到端验收（真实端口、真实调用）。"""

from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
from datetime import datetime

from xinji.server import serve

AUTUMN = datetime(2026, 10, 15)


class _Client:
    """测试用的最小 HTTP 客户端。"""

    def __init__(self, port: int):
        self.port = port

    def get(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                     {"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile, os
        fd, cls.db = tempfile.mkstemp(suffix=".db", prefix="xj-serve-")
        os.close(fd)
        os.remove(cls.db)
        cls.port = 8765 + os.getpid() % 100  # 避开并发测试端口冲突
        cls.thread = threading.Thread(
            target=serve, args=(cls.db, "127.0.0.1", cls.port), daemon=True)
        cls.thread.start()
        cls.client = _Client(cls.port)
        for _ in range(50):  # 等服务就绪
            try:
                status, _ = cls.client.get("/api/v1/health")
                if status == 200:
                    break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        # daemon 线程随进程退出；测试进程内不再复用
        pass

    def test_health_and_full_loop(self):
        """健康检查 + 听见/读取/治理 全链路。"""
        status, health = self.client.get("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["api"], "xinji-api/v1")

        # 写路径：原话入账 + 整理
        status, ack = self.client.post("/api/v1/hear",
                                       {"text": "我想在秋天去看海。"})
        self.assertEqual(status, 200)
        self.assertGreater(ack["seq"], 0)

        status, _ = self.client.post("/api/v1/hear",
                                     {"text": "到时候别催我订计划，我不想被追问。"})
        self.assertEqual(status, 200)

        # 读路径：边界下默认沉默
        status, out = self.client.post("/api/v1/read",
                                       {"input": "最近降温了，有点想喝热奶茶。",
                                        "now": AUTUMN.isoformat()})
        self.assertEqual(status, 200)
        pack = out["pack"]
        self.assertEqual(pack["memories"], [])          # 默认沉默
        reasons = {s["judgment"] for s in pack["suppressed"]}
        self.assertIn("boundary-proactive", reasons)

        # 她主动提起 → 允许接上
        status, out = self.client.post("/api/v1/read",
                                       {"input": "对了，我决定十月去看海！",
                                        "now": AUTUMN.isoformat()})
        self.assertEqual(status, 200)
        self.assertIn("看海", [m["topic"] for m in out["pack"]["memories"]])

    def test_view_audit_export_verify(self):
        status, view = self.client.get("/api/v1/view")
        self.assertEqual(status, 200)
        self.assertGreater(len(view["memories"]), 0)
        self.assertGreaterEqual(len(view["boundaries"]), 1)

        status, audit = self.client.get("/api/v1/audit?tail=5")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(audit["events"]), 5)

        status, exported = self.client.get("/api/v1/export")
        self.assertEqual(status, 200)
        self.assertEqual(exported["format"], "xinji.export/v1")

        status, verified = self.client.get("/api/v1/verify")
        self.assertEqual(status, 200)
        self.assertTrue(verified["ok"])

    def test_delete_with_replay_verification(self):
        """删除 API 自带重放验收：删除后不复活。"""
        self.client.post("/api/v1/hear", {"text": "我想在秋天去青岛看海。"})
        _, view = self.client.get("/api/v1/view")
        sea = [m for m in view["memories"] if m["topic"] == "青岛看海"]
        self.assertGreaterEqual(len(sea), 1)
        status, result = self.client.post("/api/v1/delete",
                                          {"kind": "memory", "targets": [sea[0]["id"]],
                                           "reason": "测试删除"})
        self.assertEqual(status, 200)
        self.assertTrue(result["replay"]["ok"])

        _, out = self.client.post("/api/v1/read",
                                  {"input": "我十月去看海要带什么？",
                                   "now": AUTUMN.isoformat()})
        self.assertEqual([m for m in out["pack"]["memories"] if m["topic"] == "青岛看海"], [])

    def test_disable_degrades_read(self):
        status, _ = self.client.post("/api/v1/disable", {"reason": "测试停用"})
        self.assertEqual(status, 200)
        _, out = self.client.post("/api/v1/read", {"input": "今天好累",
                                                   "now": AUTUMN.isoformat()})
        self.assertEqual(out["pack"]["mode"], "disabled")
        _, _ = self.client.post("/api/v1/enable", {})
        _, out = self.client.post("/api/v1/read", {"input": "今天好累",
                                                   "now": AUTUMN.isoformat()})
        self.assertEqual(out["pack"]["mode"], "normal")

    def test_error_routes(self):
        status, err = self.client.post("/api/v1/nope", {})
        self.assertEqual(status, 404)
        status, err = self.client.get("/api/v1/nope")
        self.assertEqual(status, 404)
        status, err = self.client.post("/api/v1/delete", {"kind": "bogus", "targets": []})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
