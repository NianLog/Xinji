"""心记接入示例：把记忆系统接到你自己的模型上。

心记不替代模型，也不替代 RAG——它夹在模型与存储之间，负责
「组织记忆与关系，再决定说或不说」。接入只有三步：

1. 听见一句话 → ``hear()``：原话进账本，立刻确认，不阻塞；
2. 组装上下文 → ``read()``：拿到记忆包（哪些能提、怎么提、什么被压下）；
3. 模型回复后 → 把回复也 ``hear(role="assistant")`` 记进账本。

运行：``PYTHONPATH=src python examples/basic_usage.py``
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from xinji import XinjiEngine


def your_model_reply(prompt: str) -> str:
    """换成你自己的模型调用（OpenAI 兼容接口 / 本地模型 / ……）。"""
    return f"（你的模型会基于下面的上下文回复）\n{prompt}"


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="xinji-usage-")
    os.close(fd)
    os.remove(path)
    eng = XinjiEngine(path)
    try:
        # —— 春天：先记下来（写路径） ————————————————
        eng.hear("我想在秋天去看海。")
        eng.organize()  # 后台整理；也可以攒一批再整理
        eng.hear("那阵子别催我订计划，我不想被追问。")
        eng.organize()

        # —— 秋天：想好再说（读路径） ————————————————
        for text, now in [
            ("最近降温了，有点想喝热奶茶。", datetime(2026, 10, 15)),   # 她没提海
            ("对了，我决定十月去看海！", datetime(2026, 10, 15)),       # 她提了海
        ]:
            pack = eng.read(text, now=now)
            print(f"\n她：{text}")
            print(f"  记忆包：{[(m['kind'], m['judgment']) for m in pack['memories']]}")
            print(f"  指引：{pack['guidance']}")
            print(f"  被压下：{[(s['judgment']) for s in pack['suppressed']]}")
            # 把 pack['memories'] / pack['guidance'] / pack['recent_turns']
            # / pack['profile_summary'] / pack['hard_boundaries'] 拼进你的
            # 系统 prompt，再交给模型生成回复：
            reply = your_model_reply(pack["guidance"])
            eng.hear(reply, role="assistant")  # 回复同样入账

        # —— 治理：忘得掉 ————————————————
        sea = next(m for m in eng.view()["memories"] if m["topic"] == "看海")
        eng.delete_memory(sea["id"], reason="她要求删除")
        print("\n删除后重放验收：", "通过" if eng.replay()["ok"] else "失败")
    finally:
        eng.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
