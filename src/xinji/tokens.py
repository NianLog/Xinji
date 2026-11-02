"""分词与 token 预算估算（中文友好，零依赖）。

读路径的「约 8k token 预算」需要一个可复现的估算器：

- 中日韩字符按 1 token/字估算；
- 其余文本按约 4 字符/token 估算；
- 检索分词：ASCII 单词小写化 + CJK 二元组（bigram），单字 CJK 保留单字。
"""

from __future__ import annotations

import re

_CJK_RANGES = (
    (0x3040, 0x30FF),   # 平假名 / 片假名
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x4E00, 0x9FFF),   # CJK 基本区
    (0xF900, 0xFAFF),   # CJK 兼容表意
)

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_CHAR_RE = re.compile(
    "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CJK_RANGES) + "]"
)


def is_cjk(ch: str) -> bool:
    """判断单个字符是否属于 CJK 表意区。"""
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """把文本切成检索用词袋：ASCII 单词 + CJK 二元组（短文本退化为单字）。"""
    if not text:
        return []
    tokens: list[str] = [w.lower() for w in _ASCII_WORD_RE.findall(text)]

    cjk = _CJK_CHAR_RE.findall(text)
    if len(cjk) == 1:
        tokens.append(cjk[0])
    else:
        tokens.extend(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数（约 1 token/汉字，4 字符/token 其他文字）。"""
    if not text:
        return 0
    cjk_count = len(_CJK_CHAR_RE.findall(text))
    other = _ASCII_WORD_RE.sub(" ", text)
    other_chars = sum(1 for c in other if not c.isspace())
    return cjk_count + (other_chars + 3) // 4


def season_of(month: int) -> str:
    """月份（1-12）→ 季节，用于开放话题的时节触发。"""
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


SEASON_ZH = {
    "spring": "春天",
    "summer": "夏天",
    "autumn": "秋天",
    "winter": "冬天",
}
