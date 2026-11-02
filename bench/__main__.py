"""``python -m bench`` 评测入口。

用法::

    PYTHONPATH=src python -m bench run --synthetic 200
    PYTHONPATH=src python -m bench run --locomo bench/data/locomo10.json
    PYTHONPATH=src python -m bench run --synthetic 200 --locomo bench/data/locomo10.json
"""

from __future__ import annotations

import argparse
import json
import os

from .runner import run_synthetic, run_locomo, write_report, run_soak


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bench", description="心记关系记忆评测")
    p.add_argument("command", choices=["run", "soak"])
    p.add_argument("--synthetic", type=int, default=0,
                   help="合成基准对话数（0 = 跳过）")
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--locomo", default=None, help="locomo10.json 路径（缺省跳过）")
    p.add_argument("--strategies", default=None,
                   help="逗号分隔，默认全部：full-context,sliding-window,summary,oag,xinji")
    p.add_argument("--out", default="bench/results", help="结果目录")
    p.add_argument("--report", default="bench/BENCH_REPORT.md", help="报告文件")
    # soak 参数
    p.add_argument("--rounds", type=int, default=5, help="soak 轮数")
    p.add_argument("--conversations", type=int, default=1000, help="soak 每轮对话数")
    p.add_argument("--seeds", default=None, help="soak 种子列表（逗号分隔，缺省 2026+i*7）")
    args = p.parse_args(argv)

    names = args.strategies.split(",") if args.strategies else None

    if args.command == "soak":
        seeds = ([int(s) for s in args.seeds.split(",")] if args.seeds else None)
        print(f"[soak] 连续压测：{args.rounds} 轮 × {args.conversations} 段对话，"
              "校验心记全部硬不变量。")
        result = run_soak(rounds=args.rounds, conversations=args.conversations,
                          seeds=seeds, strategy_names=names)
        _dump(result, os.path.join(args.out, "soak.json"))
        for r in result["results"]:
            print(f"  seed={r['seed']:<6} {'✅ PASS' if r['ok'] else '❌ FAIL'} "
                  f"recall={r['metrics']['recall_macro']} viol={r['metrics']['violation_macro']} "
                  f"resurrect={r['metrics']['resurrection_macro']} "
                  f"stage={r['metrics']['stage_accuracy']}")
        print(f"[soak] 结论：{'全部轮次通过，未发现违例。' if result['all_ok'] else '存在违例！'}")
        return 0 if result["all_ok"] else 1

    names = args.strategies.split(",") if args.strategies else None
    synthetic = locomo = None
    if args.synthetic > 0:
        print(f"[bench] 合成基准：{args.synthetic} 段对话（seed={args.seed}）…")
        synthetic = run_synthetic(seed=args.seed, n_conversations=args.synthetic,
                                  strategy_names=names)
        _dump(synthetic, os.path.join(args.out, "synthetic.json"))
        _print_summary(synthetic["strategies"], ["recall_macro", "violation_macro",
                                                 "resurrection_macro", "avg_tokens"])
    if args.locomo:
        print(f"[bench] LoCoMo 真实数据：{args.locomo}")
        locomo = run_locomo(args.locomo, strategy_names=names)
        _dump(locomo, os.path.join(args.out, "locomo.json"))
        _print_summary(locomo["strategies"], ["recall", "induced_exposure", "avg_tokens"])
    if synthetic or locomo:
        report = write_report(synthetic, locomo, args.report)
        print(f"[bench] 报告已写入 {args.report}（{len(report.splitlines())} 行）")
    else:
        p.error("至少指定 --synthetic N 或 --locomo PATH")
    return 0


def _dump(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[bench] 结果已写入 {path}")


def _print_summary(strategies: dict, keys: list[str]) -> None:
    for name, m in strategies.items():
        parts = [f"{k}={m.get(k)}" for k in keys if m.get(k) is not None]
        print(f"  {name:<15} {' '.join(parts)}")


if __name__ == "__main__":
    raise SystemExit(main())
