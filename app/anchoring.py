"""引用锚定：给每条引用找一句"答案里确实出现"的锚文本。

为什么做**软锚定**、不要求模型输出 `[n]` 标记：
让模型自己标 `[1][2]` 有两个不可控风险——
1. 标出界（`[3]` 但引用只有 2 条）；
2. 更糟：**标错**（把不支持某句话的来源标成它的出处）。

对学术产品，引用被张冠李戴比"没锚定"更致命。所以这里只做"宁缺勿错"的软锚定：
在答案里**逐字查找**引用的人名/论文名，找到了才挂一个上标；找不到就退化成底部列表。
绝不生成答案里没有的文本。
"""
from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{5,}|\d{4}")


def anchor_citations(answer: str, citations: list[dict]) -> list[dict]:
    """给 citations 里的每条附一个 `anchor`（答案里确实存在的片段）或 None。"""
    alow = (answer or "").lower()
    out: list[dict] = []
    for c in citations or []:
        c = dict(c)
        label = (c.get("label") or "").strip()
        anchor = None
        for nd in _needles(label):
            if nd.lower() in alow:
                anchor = nd
                break
        c["anchor"] = anchor
        out.append(c)
    return out


def _needles(label: str) -> list[str]:
    """候选锚文本：先完整 label，太长（论文标题）则退化为最长的实词。"""
    if not label:
        return []
    needles = [label]
    if len(label) > 40:
        toks = sorted({w for w in _WORD.findall(label) if len(w) >= 6},
                      key=len, reverse=True)
        needles = toks[:3] + [label]
    return needles
