"""对话上下文：让"那这些人里…"能指代上一轮的结果。

为什么不用 LLM 记上下文：我们的答案链路是确定性的（解析→过滤→排序），
上下文只需要存"上一轮命中了哪些 pid + 条件是什么"，存成结构化数据比塞进提示词更可靠。

设计：
- 每个 sid（前端生成）保留最近几轮，存在 SQLite 里（单 worker，重启不丢）；
- 指代词（这些人/他们/上述/其中…）触发继承：把上一轮的 pid 集合作为过滤条件；
- 排序意图（谁被引最高/论文最多）应用到继承后的集合上，而不是全库。
"""
from __future__ import annotations

import json
import time

from app.db import connect, init_db

# 指代词：出现任意一个就认为"在说上一轮的结果"
ANAPHORA = ("这些人", "这批人", "这几位", "他们", "它们", "上述", "上面", "刚才",
            "其中", "里面", "这几个", "这俩", "那几位", "前面提到")

# 针对"这一批人"的排序意图
SORT_HINTS = [
    ("cites", ("被引最高", "引用最高", "引用最多", "被引最多", "影响力最大", "最权威")),
    ("works", ("论文最多", "作品最多", "发表最多", "产出最多")),
    ("recent", ("最年轻", "年轻", "最近活跃", "近年活跃")),
]

DDL = """
CREATE TABLE IF NOT EXISTS chat_ctx (
  sid    TEXT PRIMARY KEY,
  ts     REAL NOT NULL,
  query  TEXT,
  pids   TEXT NOT NULL,      -- JSON 数组
  facets TEXT NOT NULL       -- JSON 对象
);
"""

MAX_KEEP = 60  # 最多保留多少个 sid（防止无限增长）


def init() -> None:
    init_db()
    conn = connect()
    try:
        conn.execute(DDL)
        conn.commit()
    finally:
        conn.close()


def has_anaphora(query: str) -> bool:
    q = query or ""
    return any(w in q for w in ANAPHORA)


def sort_hint(query: str) -> str | None:
    q = query or ""
    for name, words in SORT_HINTS:
        if any(w in q for w in words):
            return name
    return None


def save(sid: str, query: str, pids: list[int], facets: dict) -> None:
    if not sid:
        return
    init()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO chat_ctx(sid, ts, query, pids, facets) VALUES(?,?,?,?,?) "
            "ON CONFLICT(sid) DO UPDATE SET ts=excluded.ts, query=excluded.query, "
            "pids=excluded.pids, facets=excluded.facets",
            (sid, time.time(), query, json.dumps(pids[:200]), json.dumps(facets, ensure_ascii=False)))
        conn.execute(
            "DELETE FROM chat_ctx WHERE sid NOT IN "
            "(SELECT sid FROM chat_ctx ORDER BY ts DESC LIMIT ?)", (MAX_KEEP,))
        conn.commit()
    finally:
        conn.close()


def load(sid: str) -> dict | None:
    if not sid:
        return None
    init()
    conn = connect()
    try:
        row = conn.execute("SELECT query, pids, facets, ts FROM chat_ctx WHERE sid=?", (sid,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    # 30 分钟没说话就当新会话，避免答非所问
    if time.time() - float(row["ts"]) > 1800:
        return None
    return {"query": row["query"], "pids": json.loads(row["pids"] or "[]"),
            "facets": json.loads(row["facets"] or "{}")}


def resolve(query: str, sid: str) -> dict:
    """判断要不要继承上一轮，以及继承什么。

    返回 {"inherit": bool, "pids": [...], "sort": str|None, "note": str, "prev_query": str}
    """
    out = {"inherit": False, "pids": [], "sort": None, "note": "", "prev_query": ""}
    if not sid or not has_anaphora(query):
        return out
    prev = load(sid)
    if not prev or not prev["pids"]:
        return out
    out.update(inherit=True, pids=prev["pids"], prev_query=prev["query"] or "")
    hint = sort_hint(query)
    if hint:
        out["sort"] = hint
        if hint == "recent" and "年轻" in query:
            # 库里没有出生年份，不能假装能算年龄
            out["note"] = "库里没有出生年份，无法按年龄排序；这里按最近活跃度排。"
        else:
            out["note"] = f"在这 {len(prev['pids'])} 人里按{hint}排序。"
    else:
        out["note"] = f"沿用上一轮的 {len(prev['pids'])} 人。"
    return out


def clear(sid: str) -> None:
    if not sid:
        return
    init()
    conn = connect()
    try:
        conn.execute("DELETE FROM chat_ctx WHERE sid=?", (sid,))
        conn.commit()
    finally:
        conn.close()
