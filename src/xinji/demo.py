"""README「一个场景」的可运行版本：她说想秋天去看海，也说过别催她。

时间线：

1. 春天，她许下愿望：「我想在秋天去看海。」
   → 写路径：原话进 L0，整理出事实记忆 + 开放话题（时节触发：秋天）。
2. 她补了一句：「那阵子别催我订计划，我不想被追问。」
   → 整理器把它提取成 L2 硬边界（挂在「看海」话题上，带原话证据）。
3. 秋天到了，她聊降温。普通记忆系统会追问「看海计划怎么样了？」；
   心记先看边界——「别催我」仍有效——于是它记得海，但保持安静
   （默认沉默）。
4. 她自己提起看海。这时是用户主动，记忆允许装包，且附带「分寸」：
   自然接上，但不追问计划。
5. 治理：删除这条记忆 → 补整理 → 重放验收，它没有复活（忘得掉）。
6. 一键停用：读路径降级为「最近几轮 + 画像 + 硬边界」，聊天照常。
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from .cli import demo_reply, print_pack
from .engine import XinjiEngine

SPRING = datetime(2026, 4, 12)
AUTUMN = datetime(2026, 10, 15)


def _banner(title: str) -> None:
    print()
    print("=" * 62)
    print(title)
    print("=" * 62)


def run_autumn_sea(db_path: str = None, keep: bool = False) -> None:
    tmp = None
    if db_path is None:
        fd, tmp = tempfile.mkstemp(suffix=".db", prefix="xinji-demo-")
        os.close(fd)
        os.remove(tmp)
        db_path = tmp
    eng = XinjiEngine(db_path)

    try:
        _banner("① 春天 · 她说：我想在秋天去看海。")
        ack = eng.hear("我想在秋天去看海。")
        print(f"写路径确认：原话已入账本（seq {ack['seq']}，第 {ack['turn']} 轮）——"
              "不阻塞聊天，整理随后进行。")
        eng.organize()
        for m in eng.store.memories():
            print(f"  整理出 [{m['kind']}] {m['content']}（topic={m['topic'] or '-'}）")
        for t in eng.view()["open_topics"]:
            print(f"  开放话题：{t['topic']}｜时节触发 {t['trigger']}")

        _banner("② 春天 · 她又说：那阵子别催我订计划，我不想被追问。")
        eng.hear("那阵子别催我订计划，我不想被追问。")
        eng.organize()
        for b in eng.hard_boundaries():
            print(f"  边界（L2）：{b['topic']} —— {b['directive']}")

        _banner("③ 秋天 · 她聊降温（她没有提起海）")
        text = "最近降温了，有点想喝热奶茶。"
        eng.hear(text)
        eng.organize()
        pack = eng.read(text, now=AUTUMN)
        print(f"她 > {text}")
        print_pack(pack)
        reply = demo_reply(pack, text)
        eng.hear(reply, role="assistant")
        print(f"心记 > {reply}")
        print("  ↑ 检索其实「想起」了看海（时节触发），但边界先于相似度：")
        print("    「别催我」仍有效，所以压下不提——记得，但不把旧愿望变成压力。")

        _banner("④ 秋天 · 她自己提起了海")
        text = "对了，我决定十月去看海！"
        eng.hear(text)
        eng.organize()
        pack = eng.read(text, now=AUTUMN)
        print(f"她 > {text}")
        print_pack(pack)
        reply = demo_reply(pack, text)
        eng.hear(reply, role="assistant")
        print(f"心记 > {reply}")

        _banner("⑤ 治理 · 她要求删掉看海记忆（忘得掉）")
        sea = [m for m in eng.view()["memories"] if m["topic"] == "看海"]
        for m in sea:
            result = eng.delete_memory(m["id"], reason="她要求删除")
            print(f"  删除记忆 {m['id']}（墓碑事件 seq {result['event']}）")
        eng.organize()  # 补整理：验证不复活
        replay = eng.replay()
        pack = eng.read("十月去看海要带什么？", now=AUTUMN)
        print(f"  删除后读取：看海记忆 {len([m for m in pack['memories'] if m['topic'] == '看海'])} 条；"
              f"重放验收：{'通过，没有复活' if replay['ok'] else '失败！' + str(replay['differences'])}")
        print("  （删的是整理出的记忆；若要抹去原话，用 delete message——")
        print("    L0 原件永远只追加，删除一律以墓碑事件生效并全程可审计。）")

        _banner("⑥ 一键停用 · 先降级，不装记得")
        eng.disable()
        pack = eng.read("今天好累啊。", now=AUTUMN)
        print_pack(pack)
        print("  ↑ 关掉可选旧事，只带最近几轮、画像摘要与硬边界，聊天照常。")

        _banner("⑦ 账本 · 一本可追溯的账")
        for r in eng.audit():
            print(f"  {r['seq']:>3}  {r['type']:<18} {r['summary']}")
        broken = eng.verify_ledger()
        print(f"  哈希链校验：{'完好' if not broken else '损坏 ' + str(broken)}")
    finally:
        eng.close()
        if tmp and not keep:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp + suffix)
                except FileNotFoundError:
                    pass
        elif tmp and keep:
            print(f"\n（演示库保留在 {tmp}）")
        print()
        print("场景结束。用 `PYTHONPATH=src python -m xinji chat` 可以亲自和她聊。")
