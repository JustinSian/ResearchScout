"""chunk 级检索：命中句块 → 聚合到论文 → 聚合到作者。

为什么这样聚合：用户问的是"谁研究 X"，但证据在论文的具体句子里。
按"该人的句块最高分 + 命中块数"排序，比只看人物卡片更接近真实需求。
"""
from __future__ import annotations

import sqlite3

from app.db import connect
from app.search import _fts_query


def search_chunks(q: str, limit: int = 50,
                  conn: sqlite3.Connection | None = None) -> list[tuple[int, float]]:
    fq = _fts_query(q)  # 把问题转换成FTS查询表达式
    if not fq:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(  # 使用bm25在全文索引中搜索
            "SELECT rowid AS cid, bm25(chunks_fts) AS s FROM chunks_fts "
            "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?", (fq, limit)).fetchall()
        return [(int(r["cid"]), -float(r["s"])) for r in rows]
    finally:
        if own:
            conn.close()

# 从片段找到作者
def chunks_to_people(chunk_hits: list[tuple[int, float]], limit: int = 10,
                     conn: sqlite3.Connection | None = None) -> list[tuple[int, float]]:
    """块 → 作者：score = 该人所有命中块的最高分 + 0.1×命中块数（鼓励多块命中）。"""
    if not chunk_hits:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        best: dict[int, float] = {}
        cnt: dict[int, int] = {}
        for cid, score in chunk_hits:
            row = conn.execute("SELECT wid FROM chunks WHERE cid=?", (cid,)).fetchone() # 从片段ID找论文ID
            if not row:
                continue
            for a in conn.execute("SELECT pid FROM work_authors WHERE wid=?", (row["wid"],)): # 从论文ID找作者
                pid = int(a["pid"])
                best[pid] = max(best.get(pid, 0.0), score) 
                cnt[pid] = cnt.get(pid, 0) + 1
        ranked = sorted(((pid, s + 0.1 * cnt[pid]) for pid, s in best.items()),
                        key=lambda kv: -kv[1])[:limit]
        return ranked
    finally:
        if own:
            conn.close()

# 把两步包起来
def search_people_via_chunks(q: str, limit: int = 10,
                             chunk_limit: int = 50) -> list[tuple[int, float]]:
    conn = connect()
    try:
        hits = search_chunks(q, limit=chunk_limit, conn=conn)
        return chunks_to_people(hits, limit=limit, conn=conn)
    finally:
        conn.close()

# 为指定专家找回答证据  专家已经确定 只在这个专家参与的论文里找最相关的片段
def passages_for(pid: int, q: str, k: int = 3) -> list[dict]:
    """给某位专家挑出"最能支撑这个问题"的句子（Agent 引用用）。"""
    conn = connect()
    try:
        fq = _fts_query(q)
        if not fq:
            return []
        rows = conn.execute(
            """SELECT c.text, w.title, w.year FROM chunks_fts f
               JOIN chunks c ON c.cid = f.rowid
               JOIN works w ON w.wid = c.wid
               JOIN work_authors wa ON wa.wid = c.wid
               WHERE chunks_fts MATCH ? AND wa.pid = ?
               ORDER BY bm25(chunks_fts) LIMIT ?""", (fq, pid, k)).fetchall()
        return [{"text": r["text"][:240], "title": r["title"], "year": r["year"]}
                for r in rows]
    finally:
        conn.close()
