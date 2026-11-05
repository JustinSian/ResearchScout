"""主题一致性：判断检索出来的专家，是否真的属于用户问的那个主题。

为什么需要它：
  1. 经验记忆只在"结果确实相关"时才写入，否则会把失败路线固化下来；
  2. 库扩充的触发条件是"结果里没有相关专家"，而不是"回答措辞像拒答"。

判据（保守、可解释）：专家在 `people.topics` 里的任一标签包含主题（大小写不敏感），
或主题包含标签（处理"Krabbe disease" vs "Krabbe"这种情况）。
"""
from __future__ import annotations

import json
import re


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_]+", " ", (s or "").lower()).strip()


def topics_of(pids: list[int]) -> dict[int, list[str]]:
    if not pids:
        return {}
    from app.db import connect
    marks = ",".join("?" * len(pids[:50]))
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT pid, topics FROM people WHERE pid IN ({marks})",
            tuple(int(p) for p in pids[:50])).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        try:
            ts = json.loads(r["topics"] or "[]")
        except Exception:
            ts = []
        out[int(r["pid"])] = [str(t) for t in ts if t]
    return out


def matches(topic: str, pids: list[int]) -> bool:
    """这些专家里，是否至少有一个的标签和主题对得上。"""
    t = _norm(topic)
    if not t or not pids:
        return False
    for _pid, ts in topics_of(pids).items():
        for tag in ts:
            g = _norm(tag)
            if not g:
                continue
            if t in g or g in t:
                return True
    return False


def relevant_pids(topic: str, items: list[dict]) -> list[int]:
    """从工具结果里挑出主题相关的 pid（用于经验记录）。"""
    pids = [int(it["pid"]) for it in items if it.get("pid")]
    if not pids:
        return []
    t = _norm(topic)
    out = []
    for pid, ts in topics_of(pids).items():
        if not t:
            out.append(pid)
            continue
        if any(t in _norm(tag) or _norm(tag) in t for tag in ts if tag):
            out.append(pid)
    return out

def matched_count(topic: str, pids: list[int]) -> int:
    """这些专家里有几个的标签和主题对得上（0 表示全不对题）。

    比 matches() 的二值判断更有用：噪音查询常常"混进 1 个对的 + 4 个错的"，
    二值会判 True，比例才能看出"大部分不对题"。
    """
    t = _norm(topic)
    if not t or not pids:
        return 0
    n = 0
    for _pid, ts in topics_of(pids).items():
        hit = False
        for tag in ts:
            g = _norm(tag)
            if g and (t in g or g in t):
                hit = True
                break
        if hit:
            n += 1
    return n