"""心记开放接入服务：零依赖 HTTP JSON API（`xinji-api/v1`）。

任何宿主（DSH 插件、酒馆扩展、QQ 大肥鱼、自研客户端）都可以通过本服务
接入心记。核心概念：**一段关系一颗心（heart）**——

- **单心模式**（``--db companion.db``）：所有请求路由到同一颗心；
- **多心模式**（``--hearts-dir ./hearts``）：请求头 ``X-Xinji-Heart: <id>``
  （或 query ``?heart=<id>``）把不同用户 / 会话路由到不同的心（独立 db）。
  这是 QQ 群聊等多人场景的安全底线：**绝不能把 A 的记忆注入给 B**。

端点一览：

- POST /api/v1/hear     写路径：原话入账（+整理）
- POST /api/v1/read     读路径：拿记忆包
- POST /api/v1/organize, /correct, /delete, /stage, /boundary, /disable, /enable, /recover, /replay
- GET  /api/v1/view, /audit, /export, /verify, /health, /hearts
- POST /api/v1/backup   导出指定心（JSON 备份）

仅标准库实现；SQLite 连接跨线程共享，全部引擎调用经锁串行化（写路径的
账本追加语义本来就应当串行）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

API_VERSION = "xinji-api/v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642
DEFAULT_HEART = "default"

# heart id：防路径穿越与任意文件写入的唯一防线，必须严格白名单
HEART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class HeartError(ValueError):
    """heart id 非法或不存在。"""


class _EngineProxy:
    """一颗心的线程安全门面（单把锁串行化所有引擎调用）。

    刻意用不可重入的 Lock：在途保护（LRU 驱逐时 acquire(blocking=False)
    失败即跳过）只有对「真正持锁的在途调用」诚实才有意义。
    """

    def __init__(self, engine, heart_id: str):
        self.engine = engine
        self.heart_id = heart_id
        self.lock = threading.Lock()
        self.last_used = time.monotonic()

    def call(self, method: str, *args, **kwargs) -> Any:
        self.last_used = time.monotonic()
        with self.lock:
            return getattr(self.engine, method)(*args, **kwargs)

    def close(self) -> None:
        with self.lock:
            self.engine.close()


class HeartRouter:
    """heart id → 引擎。多心模式按文件懒加载 + LRU 池管理。"""

    def __init__(self, db_path: Optional[str] = None, hearts_dir: Optional[str] = None,
                 max_open: int = 64):
        if (db_path is None) == (hearts_dir is None):
            raise ValueError("db_path 与 hearts_dir 必须二选一")
        from .engine import XinjiEngine

        self.hearts_dir = hearts_dir
        self.db_path = db_path
        self.max_open = max_open
        self.lock = threading.RLock()
        self._open: "OrderedDict[str, _EngineProxy]" = OrderedDict()
        if hearts_dir is None:
            self._single = _EngineProxy(XinjiEngine(db_path), DEFAULT_HEART)
        else:
            import os
            os.makedirs(hearts_dir, exist_ok=True)
            self._single = None

    # ---- 解析 -----------------------------------------------------------------

    def normalize(self, heart_id: Optional[str]) -> str:
        """校验并归一化 heart id（非法即拒绝——路径穿越的唯一防线）。

        None / 缺省 → 默认心；显式传入的空白或非法 id 一律拒绝。
        """
        if heart_id is None:
            return DEFAULT_HEART
        hid = str(heart_id).strip()
        if not HEART_ID_RE.fullmatch(hid):
            raise HeartError(
                f"非法 heart id：{heart_id!r}（只允许 1–64 位字母/数字/下划线/连字符）")
        return hid

    def resolve(self, heart_id: Optional[str]) -> _EngineProxy:
        hid = self.normalize(heart_id)
        if self.hearts_dir is None:
            return self._single  # 单心模式：所有 id 都路由到唯一的心
        import os
        with self.lock:
            proxy = self._open.get(hid)
            if proxy is not None:
                self._open.move_to_end(hid)
                return proxy
            from .engine import XinjiEngine
            path = os.path.join(self.hearts_dir, f"{hid}.db")
            proxy = _EngineProxy(XinjiEngine(path), hid)
            self._open[hid] = proxy
            self._evict_locked()
            return proxy

    def _evict_locked(self) -> None:
        """超过池上限时关闭最久未用的心（只在无在途调用时关闭）。"""
        while len(self._open) > self.max_open:
            idle = []
            for hid, proxy in self._open.items():
                if proxy.lock.acquire(blocking=False):
                    idle.append((proxy.last_used, hid))
                    proxy.lock.release()
            if not idle:
                return  # 全部在途：本轮不驱逐
            idle.sort()
            victim = self._open.pop(idle[0][1])
            with victim.lock:
                victim.engine.close()

    def list_hearts(self) -> list[str]:
        import os
        if self.hearts_dir is None:
            return [DEFAULT_HEART]
        with self.lock:
            open_ids = list(self._open)
        files = sorted(f[:-3] for f in os.listdir(self.hearts_dir)
                       if f.endswith(".db"))
        return sorted(set(files) | {h for h in open_ids if h != DEFAULT_HEART})

    def backup(self, heart_id: Optional[str], out_dir: Optional[str] = None) -> str:
        """把一颗心导出为 JSON 备份（安全导出：已删除原话以墓碑占位）。"""
        hid = self.normalize(heart_id)
        proxy = self.resolve(hid)
        data = proxy.call("export")
        if out_dir is None:
            out_dir = (self.hearts_dir or ".") + "/backups"
        import os
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"{hid}-{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def close_all(self) -> None:
        with self.lock:
            for proxy in self._open.values():
                proxy.close()
            self._open.clear()
            if self._single is not None:
                self._single.close()


class XinjiAPIHandler(BaseHTTPRequestHandler):
    """xinji-api/v1 请求处理器。router 由 server 实例持有。"""

    server_version = "xinji-serve/1.2.0"
    protocol_version = "HTTP/1.1"

    @property
    def router(self) -> HeartRouter:
        return self.server.router  # type: ignore[attr-defined]

    # ---- 基础设施 ---------------------------------------------------------------

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _heart(self, qs: dict[str, str], body: Optional[dict[str, Any]] = None) -> str:
        """heart id：header > query > body > 默认（显式空值视为非法，不静默兜底）。"""
        hid = self.headers.get("X-Xinji-Heart")
        if hid is None and "heart" in qs:
            hid = qs["heart"]
        if hid is None and body is not None:
            hid = body.get("heart")
        return self.router.normalize(hid)

    def log_message(self, fmt: str, *args) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ---- 路由 -------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if route == "/api/v1/health":
                self._send(200, {"ok": True, "api": API_VERSION,
                                 "mode": "multi" if self.router.hearts_dir else "single"})
                return
            if route == "/api/v1/hearts":
                self._send(200, {"api": API_VERSION,
                                 "hearts": self.router.list_hearts()})
                return
            proxy = self.router.resolve(self._heart(qs))
            if route == "/api/v1/view":
                self._send(200, {**proxy.call("view"), "heart": proxy.heart_id})
                return
            if route == "/api/v1/audit":
                tail = int(qs["tail"]) if "tail" in qs else None
                self._send(200, {"api": API_VERSION,
                                 "events": proxy.call("audit", tail=tail)})
                return
            if route == "/api/v1/export":
                self._send(200, proxy.call("export", full=qs.get("full") == "1"))
                return
            if route == "/api/v1/verify":
                broken = proxy.call("verify_ledger")
                self._send(200, {"ok": not broken, "broken": broken})
                return
            self._send(404, {"error": f"未知路由 GET {route}"})
        except HeartError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": repr(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/")
        try:
            body = self._body()
            qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            heart = self._heart(qs, body)
            self._dispatch(route, body, heart)
        except HeartError as exc:
            self._send(400, {"error": str(exc)})
        except (ValueError, KeyError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": repr(exc)})

    def _dispatch(self, route: str, body: dict[str, Any], heart: str) -> None:
        proxy = self.router.resolve(heart)
        if route == "/api/v1/hear":
            ack = proxy.call("hear", str(body.get("text", "")),
                             role=str(body.get("role", "user")))
            if body.get("organize", True):
                proxy.call("organize")
            self._send(200, {"api": API_VERSION, "heart": heart, **ack})
            return
        if route == "/api/v1/read":
            now_raw = body.get("now")
            now = None
            if now_raw:
                from datetime import datetime
                now = datetime.fromisoformat(str(now_raw))
            pack = proxy.call(
                "read", str(body.get("input", "")),
                now=now, recent_limit=int(body.get("recent_limit", 6)))
            self._send(200, {"api": API_VERSION, "heart": heart, "pack": pack})
            return
        if route == "/api/v1/organize":
            self._send(200, {"api": API_VERSION, "report": proxy.call("organize")})
            return
        if route == "/api/v1/correct":
            result = proxy.call("correct_memory", str(body["memory_id"]),
                                str(body["to"]), reason=str(body.get("reason", "用户纠正")))
            self._send(200, {"api": API_VERSION, **result})
            return
        if route == "/api/v1/delete":
            kind = body.get("kind", "message")
            targets = body.get("targets", [])
            reason = str(body.get("reason", "用户删除"))
            if kind == "message":
                result = proxy.call("delete_messages",
                                    [int(s) for s in targets], reason=reason)
            elif kind == "memory":
                result = proxy.call("delete_memory", str(targets[0]), reason=reason)
            elif kind == "topic":
                result = proxy.call("delete_topic", str(targets[0]), reason=reason)
            else:
                raise ValueError(f"未知删除类型：{kind}")
            if body.get("verify", True):
                result["replay"] = proxy.call("replay")
            self._send(200, {"api": API_VERSION, **result})
            return
        if route == "/api/v1/stage":
            ev = proxy.call("set_stage", str(body["value"]),
                            reason=str(body.get("reason", "宿主操作")))
            self._send(200, {"api": API_VERSION, "event": ev.seq})
            return
        if route == "/api/v1/boundary":
            action = body.get("action", "set")
            if action == "set":
                proxy.call("relationship_op", "set_boundary",
                           {"topic": body["topic"],
                            "directive": body.get("directive", "不要主动提起")},
                           reason="宿主操作")
            elif action == "lift":
                proxy.call("relationship_op", "lift_boundary",
                           {"ref": body["ref"]}, reason="宿主操作")
            else:
                raise ValueError(f"未知边界操作：{action}")
            self._send(200, {"api": API_VERSION,
                             "boundaries": proxy.call("hard_boundaries")})
            return
        if route == "/api/v1/disable":
            self._send(200, {"api": API_VERSION,
                             **proxy.call("disable", reason=str(body.get("reason", "一键停用")))})
            return
        if route == "/api/v1/enable":
            self._send(200, {"api": API_VERSION, **proxy.call("enable")})
            return
        if route == "/api/v1/recover":
            self._send(200, {"api": API_VERSION, **proxy.call("recover")})
            return
        if route == "/api/v1/replay":
            self._send(200, {"api": API_VERSION, "replay": proxy.call("replay")})
            return
        if route == "/api/v1/backup":
            path = self.router.backup(body.get("heart", heart))
            self._send(200, {"api": API_VERSION, "heart": heart, "backup": path})
            return
        self._send(404, {"error": f"未知路由 POST {route}"})


def serve(db_path: Optional[str] = None, host: str = DEFAULT_HOST,
          port: int = DEFAULT_PORT, *, verbose: bool = False,
          hearts_dir: Optional[str] = None) -> None:
    """启动 xinji-api/v1 服务（阻塞直至 Ctrl-C）。单心 / 多心二选一。"""
    router = HeartRouter(db_path=db_path, hearts_dir=hearts_dir)
    httpd = ThreadingHTTPServer((host, port), XinjiAPIHandler)
    httpd.router = router  # type: ignore[attr-defined]
    httpd.verbose = verbose  # type: ignore[attr-defined]
    mode = (f"多心模式（目录 {hearts_dir}，X-Xinji-Heart 路由）"
            if hearts_dir else f"单心模式（db {db_path}）")
    print(f"心记服务已启动：http://{host}:{port}  （api {API_VERSION}，{mode}）")
    print("Ctrl-C 停止；账本与派生层随写随存。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        httpd.server_close()
        router.close_all()


def main_serve(db: Optional[str], host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
               verbose: bool = False, hearts_dir: Optional[str] = None) -> None:
    serve(db, host, port, verbose=verbose, hearts_dir=hearts_dir)
