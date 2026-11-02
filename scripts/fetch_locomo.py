"""下载 LoCoMo 数据集（snap-research/LoCoMo，CC BY-NC 4.0，仅研究用途）。

下载到 bench/data/locomo10.json（该目录已被 .gitignore 排除——数据集
许可证为非商业，不随 MIT 代码仓库分发；只提交下载脚本与聚合结果）。
"""

from __future__ import annotations

import os
import sys
import urllib.request

MIRRORS = [
    "https://raw.githubusercontent.com/snap-research/LoCoMo/main/data/locomo10.json",
    "https://ghproxy.net/https://raw.githubusercontent.com/snap-research/LoCoMo/main/data/locomo10.json",
]
DEST = os.path.join(os.path.dirname(__file__), "..", "bench", "data", "locomo10.json")


def main() -> int:
    dest = os.path.abspath(DEST)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        print(f"已存在：{dest}")
        return 0
    for url in MIRRORS:
        try:
            print(f"尝试 {url}")
            with urllib.request.urlopen(url, timeout=300) as resp:
                data = resp.read()
            if len(data) < 1_000_000:
                raise RuntimeError(f"文件过小（{len(data)} 字节），可能不完整")
            with open(dest, "wb") as f:
                f.write(data)
            print(f"已下载 {len(data):,} 字节 → {dest}")
            print("许可：CC BY-NC 4.0（署名-非商业性使用）。仅用于研究评测，请勿再分发。")
            return 0
        except Exception as exc:
            print(f"  失败：{exc}")
    print("所有镜像均失败；可手动下载后放到 bench/data/locomo10.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())
