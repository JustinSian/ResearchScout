"""全文切块：段落合并 + 去参考文献 + 去页眉页脚噪声。

与摘要切块的区别：
- 摘要按句号切（3.6 块/篇）；全文按段落切（通常 20~60 块/篇），块更大（600~1000 字符）；
- 必须去掉参考文献段，否则 chunk 索引里全是作者名和年份（会把噪声当成证据）。
"""
from __future__ import annotations

import re

REF_HEAD = re.compile(r"\n\s*(references|bibliography|参考文献)\s*\n", re.I)
PAGE_NO = re.compile(r"^\s*\d{1,4}\s*$")
ARXIV_STAMP = re.compile(r"^arxiv:[0-9.]+v\d+.*$", re.I | re.M)


def strip_noise(text: str) -> str:
    text = ARXIV_STAMP.sub("", text)
    m = REF_HEAD.search(text)
    if m and m.start() > len(text) * 0.35:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not PAGE_NO.match(ln)]
    return "\n".join(lines)


def split_fulltext(text: str, max_len: int = 900, min_len: int = 250) -> list[str]:
    text = strip_noise(re.sub(r"[ \t]+", " ", text or ""))
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) < 60:            # 标题、图注残片
            continue
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= max_len:
            buf = f"{buf}\n{p}"
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    out: list[str] = []
    for c in chunks:
        if out and len(c) < min_len:
            out[-1] = f"{out[-1]}\n{c}"
        else:
            out.append(c)
    # 太长的块（如整节没有空行）再硬切
    final: list[str] = []
    for c in out:
        while len(c) > max_len * 2:
            cut = c.rfind(". ", 0, max_len * 2)
            cut = cut if cut > max_len else max_len * 2
            final.append(c[:cut].strip())
            c = c[cut:].strip()
        if c:
            final.append(c)
    return final
