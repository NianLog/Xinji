"""测试公共设施。"""

from __future__ import annotations

import os
import tempfile

from xinji import XinjiEngine

SPRING = None
try:
    from datetime import datetime
    SPRING = datetime(2026, 4, 12)
    AUTUMN = datetime(2026, 10, 15)
    WINTER = datetime(2027, 1, 5)
except ImportError:  # pragma: no cover
    pass


def make_engine() -> tuple[XinjiEngine, str]:
    """在临时目录里建一个心记库。"""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="xinji-test-")
    os.close(fd)
    os.remove(path)
    return XinjiEngine(path), path


def cleanup(engine: XinjiEngine, path: str) -> None:
    engine.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def autumn_sea_fixture(engine: XinjiEngine) -> None:
    """README 场景：春天许愿看海 + 别催我。"""
    engine.hear("我想在秋天去看海。")
    engine.organize()
    engine.hear("那阵子别催我订计划，我不想被追问。")
    engine.organize()
