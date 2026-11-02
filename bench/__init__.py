"""XinjiBench：心记的关系记忆评测（合成基准 + LoCoMo 真实数据）。"""

from .runner import run_synthetic, run_locomo, write_report, BENCH_VERSION

__all__ = ["run_synthetic", "run_locomo", "write_report", "BENCH_VERSION"]
