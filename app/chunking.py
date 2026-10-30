"""按句切块（chunk 级检索的基础）。

为什么先做摘要级：OpenAlex 只返回摘要（inverted index），**不提供全文**；
某学术图谱有 30,984 个 chunk 是因为它存了 PDF 全文。所以这里先把摘要切成句块，
把"chunk → work → 作者"的链路和评测跑通；后续能拿到 OA 全文时，只需替换文本来源。

切块策略（简单但可解释）：
- 按句末标点切，合并到 160~420 字符；
- 太短的块并到前一块（避免"See also."这种无信息块）；
- 保留序号，便于引用定位（第几块）。
"""
from __future__ import annotations

import re

SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def split_abstract(text: str, min_len: int = 160, max_len: int = 420) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    sents = [s for s in SENT.split(text) if s]
    chunks: list[str] = []
    buf = ""
    for s in sents:
        if not buf:
            buf = s
        elif len(buf) + 1 + len(s) <= max_len:
            buf = f"{buf} {s}"
        else:
            chunks.append(buf)
            buf = s
    if buf:
        chunks.append(buf)
    # 合并过短的尾块
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < min_len:
            merged[-1] = f"{merged[-1]} {c}"
        else:
            merged.append(c)
    return merged
