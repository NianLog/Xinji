"""心记命令行：写入/读取演示与全套治理操作。

用法（无需安装，仓库根目录执行）::

    PYTHONPATH=src python -m xinji init
    PYTHONPATH=src python -m xinji demo            # 跑 README「秋天看海」场景
    PYTHONPATH=src python -m xinji chat            # 交互对话（离线模板回复）
    PYTHONPATH=src python -m xinji view / audit / export / replay / verify
    PYTHONPATH=src python -m xinji delete --help
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime
from typing import Optional

from . import __version__
from .engine import XinjiEngine
from .relationship import STAGES, STAGE_ZH
from .judgment import TOKEN_BUDGET, MAX_ITEMS

DEFAULT_DB = "xinji.db"

_KIND_ZH = {
    "fact": "事实", "episode": "情景", "story": "共同故事",
    "profile": "画像", "summary": "摘要",
}


def _out(*args, **kwargs) -> None:
    print(*args, **kwargs)


def _ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


# ---- 各命令 -------------------------------------------------------------------


def cmd_init(args) -> None:
    eng = XinjiEngine(args.db)
    head = eng.store.head()
    eng.close()
    _out(f"已就绪：{args.db}" + (f"（账本已有 {head[0]} 条事件）" if head else "（新账本）"))


def cmd_organize(args) -> None:
    eng = XinjiEngine(args.db)
    report = eng.organize()
    eng.close()
    _out(f"整理完成：处理 {report['processed']} 条原话，"
         f"新增 {len(report['new_memories'])} 条记忆、"
         f"{len(report['new_ops'])} 条关系变更、"
         f"{len(report['summaries'])} 份片段摘要。"
         + (f"（已降级：{report['error']}）" if report["error"] else ""))


def cmd_status(args) -> None:
    eng = XinjiEngine(args.db)
    view = eng.view()
    eng.close()
    mode_zh = {"active": "正常", "degraded": "降级中", "disabled": "已停用"}[view["mode"]]
    head = view["head"]
    _out(f"模式：{mode_zh}｜账本：{head[0] if head else 0} 条事件"
         f"（head {str(head[1])[:12] if head else '-'}）")
    _out(f"关系阶段：{STAGE_ZH[view['stage']['value']]}｜"
         f"有效边界：{len(view['boundaries'])}｜承诺：{len(view['commitments'])}｜"
         f"开放话题：{len(view['open_topics'])}")
    _out(f"L1 记忆：{len(view['memories'])} 条｜待整理原话：{view['pending_organize']} 条｜"
         f"关系版本：v{view['relationship_version']}")


def cmd_view(args) -> None:
    eng = XinjiEngine(args.db)
    view = eng.view()
    eng.close()
    mode_zh = {"active": "正常", "degraded": "降级中", "disabled": "已停用"}[view["mode"]]
    _out(f"== 心记 · {args.db} ==")
    _out(f"模式 {mode_zh}｜阶段 {STAGE_ZH[view['stage']['value']]}｜"
         f"关系版本 v{view['relationship_version']}")
    _out("\n[L2 边界]")
    for b in view["boundaries"]:
        mark = "✔有效" if b["status"] == "active" else "✘已解除"
        _out(f"  {mark} {b['topic']} —— {b['directive']}（证据 seq {b.get('evidence', [])}）")
    _out("[L2 承诺]")
    for c in view["commitments"]:
        _out(f"  {'○未兑现' if c['status'] == 'open' else '●已兑现'} {c['what']}")
    _out("[L2 开放话题]")
    for t in view["open_topics"]:
        _out(f"  {'○开着' if t['status'] == 'open' else '●已关闭'} {t['topic']}"
             + (f"｜触发：{t['trigger']}" if t.get("trigger") else ""))
    _out("[L1 记忆]")
    for m in view["memories"]:
        tags = []
        if m["corrected"]:
            tags.append("已纠正")
        if m["min_stage"]:
            tags.append(f"需≥{STAGE_ZH[STAGES[m['min_stage']]]}")
        tag = f"（{'，'.join(tags)}）" if tags else ""
        _out(f"  [{_KIND_ZH.get(m['kind'], m['kind'])}] {m['topic'] or '-'}："
             f"{m['content']}{tag}")
    _out("[墓碑]")
    if not view["tombstones"]:
        _out("  （无）")
    for t in view["tombstones"]:
        _out(f"  {t['kind']} {t['target']}（{t['reason']}，事件 seq {t['seq']}）")


def cmd_audit(args) -> None:
    eng = XinjiEngine(args.db)
    rows = eng.audit(type_=args.type, tail=args.tail)
    eng.close()
    for r in rows:
        _out(f"{r['seq']:>4}  {r['ts'][:19]}  {r['type']:<18} {r['summary']}")
    if not rows:
        _out("（账本为空）")


def cmd_export(args) -> None:
    eng = XinjiEngine(args.db)
    data = eng.export(full=args.full)
    eng.close()
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.output:
        with io.open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        _out(f"已导出到 {args.output}（{'完整' if args.full else '安全'}导出，"
             f"{len(data['events'])} 条事件）")
    else:
        _out(text)


def cmd_replay(args) -> None:
    eng = XinjiEngine(args.db)
    broken = eng.verify_ledger()
    if broken:
        _out(f"账本哈希链损坏，无法安全重放：seq {broken}")
        eng.close()
        return
    result = eng.replay(verify=not args.no_verify)
    eng.close()
    if result["ok"]:
        _out("重放验收通过：从 L0 重建的状态与现场完全一致，"
             "没有已删除内容复活，也没有状态凭空出现。")
    else:
        _out("重放验收失败，差异如下：")
        for d in result["differences"]:
            _out(f"  - {d['field']}:\n    现场: {json.dumps(d['live'], ensure_ascii=False)[:300]}\n"
                 f"    重放: {json.dumps(d['replay'], ensure_ascii=False)[:300]}")


def cmd_verify(args) -> None:
    eng = XinjiEngine(args.db)
    broken = eng.verify_ledger()
    eng.close()
    _out("账本哈希链完好，未发现篡改。" if not broken
         else f"账本损坏位置：seq {broken}")


def cmd_delete(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        if args.what == "message":
            result = eng.delete_messages(args.targets, reason=args.reason)
        elif args.what == "memory":
            result = eng.delete_memory(args.targets[0], reason=args.reason)
        else:
            result = eng.delete_topic(args.targets[0], reason=args.reason)
        eng.organize()  # 删除后立即补整理，验证不复活
        replay = eng.replay()
        _out(f"已删除（墓碑事件 seq {result['event']}），撤下记忆 "
             f"{len(result['dropped_memories'])} 条。")
        _out("重放验收：" + ("通过，未复活。" if replay["ok"] else
                            f"失败！{replay['differences']}"))
    finally:
        eng.close()


def cmd_correct(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        result = eng.correct_memory(args.memory_id, args.to, reason=args.reason)
        _out(f"已纠正记忆 {args.memory_id}（事件 seq {result['event']}，旧内容保留在账本可审计）。")
    finally:
        eng.close()


def cmd_stage(args) -> None:
    if args.value not in STAGES:
        _out(f"未知阶段 {args.value}，可选：{'、'.join(f'{s}({STAGE_ZH[s]})' for s in STAGES)}")
        return
    eng = XinjiEngine(args.db)
    eng.set_stage(args.value)
    eng.close()
    _out(f"关系阶段已设为 {STAGE_ZH[args.value]}。")


def cmd_boundary(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        st = eng.state()
        if args.action == "list":
            for b in st["boundaries"]:
                mark = "✔有效" if b["status"] == "active" else "✘已解除"
                _out(f"  {mark} {b['topic']} —— {b['directive']}")
            if not st["boundaries"]:
                _out("（暂无边界）")
        elif args.action == "set":
            if not args.value:
                _out("用法：boundary set 话题 --directive 指令")
                return
            eng.relationship_op("set_boundary",
                                {"topic": args.value, "directive": args.directive},
                                reason="治理操作")
            _out(f"已设置边界：{args.value} —— {args.directive}")
        elif args.action == "lift":
            eng.relationship_op("lift_boundary", {"ref": args.value}, reason="治理操作")
            _out(f"已解除边界：{args.value}")
    finally:
        eng.close()


def cmd_topic(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        st = eng.state()
        if args.action == "list":
            for t in st["open_topics"]:
                mark = "○开着" if t["status"] == "open" else "●已关闭"
                _out(f"  {mark} {t['topic']}" + (f"｜触发 {t['trigger']}" if t.get("trigger") else ""))
            if not st["open_topics"]:
                _out("（暂无开放话题）")
        elif args.action == "close":
            eng.relationship_op("close_topic", {"ref": args.value}, reason="治理操作")
            _out(f"已关闭话题：{args.value}")
    finally:
        eng.close()


def cmd_commitment(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        st = eng.state()
        if args.action == "list":
            for c in st["commitments"]:
                mark = "○未兑现" if c["status"] == "open" else "●已兑现"
                _out(f"  {mark} {c['what']}")
            if not st["commitments"]:
                _out("（暂无承诺）")
        elif args.action == "add":
            eng.relationship_op("add_commitment", {"what": args.value}, reason="治理操作")
            _out(f"已记录承诺：{args.value}")
        elif args.action == "fulfill":
            eng.relationship_op("fulfill_commitment", {"ref": args.value}, reason="治理操作")
            _out(f"已兑现承诺：{args.value}")
    finally:
        eng.close()


def cmd_rollback(args) -> None:
    eng = XinjiEngine(args.db)
    try:
        eng.rollback_relationship(args.version)
        view = eng.view()
        _out(f"已回滚关系到 v{args.version}，当前版本 v{view['relationship_version']}。")
    finally:
        eng.close()


def cmd_disable(args) -> None:
    eng = XinjiEngine(args.db)
    eng.disable(reason=args.reason)
    eng.close()
    _out("已一键停用：读路径降级为「当前输入 + 最近几轮 + 画像摘要 + 硬边界」，聊天照常，账本继续记录。")


def cmd_enable(args) -> None:
    eng = XinjiEngine(args.db)
    eng.enable()
    eng.close()
    _out("已恢复启用。")


def cmd_recover(args) -> None:
    eng = XinjiEngine(args.db)
    result = eng.recover()
    eng.close()
    org = result["organize"]
    _out(f"已恢复并补整理：处理 {org['processed']} 条积压原话。")


# ---- 读路径演示：打印记忆包 -------------------------------------------------------


def print_pack(pack: dict, stream=_out) -> None:
    """把一个记忆包打印成可读的面板（demo/chat 共用）。"""
    mode = pack["mode"]
    if mode == "normal":
        b = pack["budget"]
        stream(f"┌─ 记忆包（第 {pack['turn']} 轮 · 阶段 {STAGE_ZH.get(pack['stage'], pack['stage'])} · "
               f"{b['items']}/{b['max_items']} 条 · {b['used']}/{b['tokens']} token）")
        for m in pack["memories"]:
            extra = ""
            if m.get("directive"):
                extra = f"｜分寸:{m['directive']['text']}"
            if m.get("trigger"):
                extra += f"｜{m['trigger']}"
            stream(f"│  · [{_KIND_ZH.get(m['kind'], m['kind'])}] {m['content']}{extra}")
        if not pack["memories"]:
            stream("│  ·（空包：默认沉默）")
        if pack["suppressed"]:
            stream("│  被压下的候选：")
            for s in pack["suppressed"]:
                stream(f"│    ✂ {s['content'][:30]}… —— {s['judgment']}：{s['detail']}")
        stream(f"│  指引：{pack['guidance']}")
        stream("└─")
    else:
        note = pack.get("note") or ""
        stream(f"┌─ 记忆包（降级 · {note}）")
        stream(f"│  旧事已关闭；画像摘要 {len(pack['profile_summary'])} 条；"
               f"硬边界 {len(pack['hard_boundaries'])} 条；最近 {len(pack['recent_turns'])} 轮在册。")
        stream("└─")


def demo_reply(pack: dict, text: str) -> str:
    """离线演示用的模板回复（未接入大模型，只演示「记得但不冒犯」的口径）。"""
    if pack["mode"] != "normal" or not pack["memories"]:
        return "嗯，我在听。（本轮没有引用任何旧记忆）"
    directive = next((m["directive"]["text"] for m in pack["memories"] if m.get("directive")), None)
    first = pack["memories"][0]["content"]
    if directive:
        return f"「{first}」我还记得——不催你，你想说的时候再说。"
    return f"我记得：{first}。"


def cmd_chat(args) -> None:
    eng = XinjiEngine(args.db)
    _out(f"心记对话演示（{args.db}）——输入 :help 查看命令，:q 退出。")
    _out("回复由离线模板生成，仅演示记忆读写路径；接入大模型时把记忆包喂给模型即可。\n")
    try:
        while True:
            try:
                text = input("她 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text in (":q", ":quit", ":exit"):
                break
            if text == ":help":
                _out(":q 退出｜:status 状态｜:view 全景｜:audit 最近事件｜:organize 立即整理")
                continue
            if text == ":status":
                cmd_status(args)
                continue
            if text == ":view":
                cmd_view(args)
                continue
            if text == ":audit":
                eng.audit()
                for r in eng.audit(tail=10):
                    _out(f"{r['seq']:>4}  {r['type']:<18} {r['summary']}")
                continue
            if text == ":organize":
                _out(json.dumps(eng.organize(), ensure_ascii=False))
                continue
            ack = eng.hear(text)
            eng.organize()
            pack = eng.read(text, now=datetime.now())
            print_pack(pack)
            reply = demo_reply(pack, text)
            eng.hear(reply, role="assistant")
            _out(f"心记 > {reply}\n")
    finally:
        eng.close()
        _out("\n再见。会话已记入账本。")


def cmd_demo(args) -> None:
    from .demo import run_autumn_sea
    # 场景演示永远在临时库上跑，不碰用户的心记库
    run_autumn_sea(keep=args.keep)


def cmd_serve(args) -> None:
    from .server import main_serve
    if not args.hearts_dir and args.db == DEFAULT_DB:
        # 服务场景没有默认账本：必须显式指定 --db 或 --hearts-dir
        import sys
        print("错误：serve 需要 --db <账本文件>（单心）或 --hearts-dir <目录>（多心，QQ 等多人场景）")
        sys.exit(2)
    main_serve(args.db if not args.hearts_dir else None,
               host=args.host, port=args.port, verbose=args.verbose,
               hearts_dir=args.hearts_dir)


# ---- 参数解析 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xinji",
        description="心记 Xinji —— 记得住，说得对，忘得掉。（关系记忆系统 V1 原型）")
    p.add_argument("--db", default=DEFAULT_DB, help=f"心记库路径（默认 ./{DEFAULT_DB}）")
    p.add_argument("--version", action="version", version=f"xinji {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化心记库").set_defaults(func=cmd_init)
    sub.add_parser("organize", help="整理未处理的原话").set_defaults(func=cmd_organize)
    sub.add_parser("status", help="一览状态").set_defaults(func=cmd_status)
    sub.add_parser("view", help="查看五层全景").set_defaults(func=cmd_view)

    pa = sub.add_parser("audit", help="审计账本事件")
    pa.add_argument("--type", dest="type", default=None, help="按事件类型过滤")
    pa.add_argument("--tail", type=int, default=None, help="只看最近 N 条")
    pa.set_defaults(func=cmd_audit)

    pa = sub.add_parser("export", help="导出 JSON（默认安全导出，已删除原话以墓碑占位）")
    pa.add_argument("-o", "--output", default=None, help="写入文件（缺省打印到终端）")
    pa.add_argument("--full", action="store_true", help="完整导出（含被删原话与哈希，用于验证）")
    pa.set_defaults(func=cmd_export)

    pa = sub.add_parser("replay", help="从 L0 重放整颗心并验收（删除不复活）")
    pa.add_argument("--no-verify", action="store_true", help="只重放不比对")
    pa.set_defaults(func=cmd_replay)

    sub.add_parser("verify", help="校验账本哈希链").set_defaults(func=cmd_verify)

    pa = sub.add_parser("delete", help="删除（消息 / 记忆 / 话题），删除后自动补整理并重放验收")
    pa.add_argument("what", choices=["message", "memory", "topic"])
    pa.add_argument("targets", nargs="+", help="消息 seq / 记忆 id / 话题名")
    pa.add_argument("--reason", default="用户删除")
    pa.set_defaults(func=cmd_delete)

    pa = sub.add_parser("correct", help="纠正一条记忆（旧内容留账可审计）")
    pa.add_argument("memory_id")
    pa.add_argument("--to", required=True, help="纠正后的内容")
    pa.add_argument("--reason", default="用户纠正")
    pa.set_defaults(func=cmd_correct)

    pa = sub.add_parser("stage", help="设置关系阶段")
    pa.add_argument("value", choices=STAGES)
    pa.set_defaults(func=cmd_stage)

    pa = sub.add_parser("boundary", help="边界管理")
    pa.add_argument("action", choices=["list", "set", "lift"])
    pa.add_argument("value", nargs="?", default=None, help="set: 话题；lift: 话题或 id")
    pa.add_argument("--directive", default="不要主动提起", help="set: 边界指令")
    pa.set_defaults(func=cmd_boundary)

    pa = sub.add_parser("topic", help="开放话题管理")
    pa.add_argument("action", choices=["list", "close"])
    pa.add_argument("value", nargs="?", default=None)
    pa.set_defaults(func=cmd_topic)

    pa = sub.add_parser("commitment", help="承诺管理")
    pa.add_argument("action", choices=["list", "add", "fulfill"])
    pa.add_argument("value", nargs="?", default=None)
    pa.set_defaults(func=cmd_commitment)

    pa = sub.add_parser("rollback", help="回滚关系档案到指定版本")
    pa.add_argument("version", type=int)
    pa.set_defaults(func=cmd_rollback)

    sub.add_parser("disable", help="一键停用（读路径降级，聊天照常）").set_defaults(func=cmd_disable)
    sub.add_parser("enable", help="恢复启用").set_defaults(func=cmd_enable)
    sub.add_parser("recover", help="从故障恢复并补整理").set_defaults(func=cmd_recover)

    pa = sub.add_parser("chat", help="交互对话演示")
    pa.set_defaults(func=cmd_chat)

    pa = sub.add_parser("demo", help="跑 README「秋天看海」场景")
    pa.add_argument("--keep", action="store_true", help="保留演示库文件（默认临时）")
    pa.set_defaults(func=cmd_demo)

    pa = sub.add_parser("serve", help="启动开放接入 HTTP 服务（xinji-api/v1）")
    pa.add_argument("--host", default="127.0.0.1")
    pa.add_argument("--port", type=int, default=8642)
    pa.add_argument("--hearts-dir", dest="hearts_dir", default=None,
                    help="多心模式：每段关系一颗心的目录（与 --db 二选一）")
    pa.add_argument("--verbose", action="store_true", help="打印请求日志")
    pa.set_defaults(func=cmd_serve)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    _ensure_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
