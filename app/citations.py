"""库内引用关系查询（对标 AMiner 的 `paper/relation`）。

数据来自 `work_refs`（app/fetch_refs.py 抓的 OpenAlex `referenced_works`）：
- `refs_of(wid)`    这篇论文引用了谁（库内可展开标题，库外只给 id）
- `citing_of(wid)`  库里有哪些论文引用了它
- `stats(wid)`      引用数 / 库内引用数
- `top_cited()`     库内被引最多的论文

诚实边界：只有被引论文**也在本库**时才能显示标题与作者；
库外引用只保留 openalex id，不伪造信息。
"""
from __future__ import annotations

from app.db import connect


def _work(conn, wid: int) -> dict | None:
    row = conn.execute(
        "SELECT wid,title,year,cites,venue,doi,openalex_id FROM works WHERE wid=?", (wid,)
    ).fetchone()
    return dict(row) if row else None


def stats(wid: int) -> dict:
    conn = connect()
    try:
        w = _work(conn, wid)
        if not w:
            return {"found": False}
        total = conn.execute("SELECT COUNT(*) FROM work_refs WHERE wid=?", (wid,)).fetchone()[0]
        in_corpus = conn.execute(
            "SELECT COUNT(*) FROM work_refs WHERE wid=? AND ref_wid IS NOT NULL", (wid,)
        ).fetchone()[0]
        citing = conn.execute(
            "SELECT COUNT(*) FROM work_refs WHERE ref_wid=?", (wid,)
        ).fetchone()[0]
        return {"found": True, "work": w, "refs_total": total,
                "refs_in_corpus": in_corpus, "cited_by_in_corpus": citing}
    finally:
        conn.close()


def refs_of(wid: int, limit: int = 20) -> dict:
    """这篇论文引用了谁。库内的展开标题/年份，库外只给 id。"""
    conn = connect()
    try:
        w = _work(conn, wid)
        if not w:
            return {"found": False, "items": [], "total": 0}
        rows = conn.execute(
            """SELECT r.ref_openalex, r.ref_wid,
                      w2.title, w2.year, w2.cites, w2.venue
               FROM work_refs r
               LEFT JOIN works w2 ON w2.wid = r.ref_wid
               WHERE r.wid = ?
               ORDER BY (r.ref_wid IS NULL), w2.cites DESC
               LIMIT ?""", (wid, limit)).fetchall()
        items = [dict(r) for r in rows]
        total = conn.execute("SELECT COUNT(*) FROM work_refs WHERE wid=?", (wid,)).fetchone()[0]
        return {"found": True, "work": w, "items": items, "total": total,
                "in_corpus": sum(1 for i in items if i.get("ref_wid"))}
    finally:
        conn.close()


def citing_of(wid: int, limit: int = 20) -> dict:
    """库内有哪些论文引用了它（同 corpus 内的被引关系）。"""
    conn = connect()
    try:
        w = _work(conn, wid)
        if not w:
            return {"found": False, "items": [], "total": 0}
        rows = conn.execute(
            """SELECT w2.wid, w2.title, w2.year, w2.cites, w2.venue
               FROM work_refs r JOIN works w2 ON w2.wid = r.wid
               WHERE r.ref_wid = ?
               ORDER BY w2.cites DESC LIMIT ?""", (wid, limit)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM work_refs WHERE ref_wid=?", (wid,)).fetchone()[0]
        return {"found": True, "work": w, "items": [dict(r) for r in rows], "total": total}
    finally:
        conn.close()


def resolve_work(query: str) -> dict | None:
    """把一个标题/关键词解析成库内论文：先精确标题，再 FTS。"""
    q = (query or "").strip()
    if not q:
        return None
    if q.isdigit():
        conn = connect()
        try:
            w = _work(conn, int(q))
        finally:
            conn.close()
        return w
    conn = connect()
    try:
        row = conn.execute(
            "SELECT wid,title,year,cites,venue FROM works WHERE title LIKE ? ORDER BY cites DESC LIMIT 1",
            (f"%{q}%",)).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            "SELECT wid,title,year,cites,venue FROM works WHERE title LIKE ? ORDER BY cites DESC LIMIT 1",
            (f"%{q.split()[0]}%",)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def top_cited(limit: int = 10) -> list[dict]:
    """库内被引用次数最多（以库内引用边计）。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT w.wid, w.title, w.year, w.cites,
                      COUNT(*) AS in_corpus_cites
               FROM work_refs r JOIN works w ON w.wid = r.ref_wid
               GROUP BY w.wid ORDER BY in_corpus_cites DESC, w.cites DESC LIMIT ?""",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
