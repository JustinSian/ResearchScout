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
        out = {"found": True, "work": w, "refs_total": total,
               "refs_in_corpus": in_corpus, "cited_by_in_corpus": citing}
        # PageRank 与直接计数是两种口径，一起给，便于对照（见 docs/m39）
        try:
            row = conn.execute(
                "SELECT score, in_corpus_cited_by FROM work_rank WHERE wid=?", (wid,)
            ).fetchone()
            if row:
                pct = conn.execute(
                    "SELECT ROUND(100.0*SUM(CASE WHEN score<=? THEN 1 ELSE 0 END)/COUNT(*), 2) "
                    "FROM work_rank", (row[0],)).fetchone()[0]
                out["pagerank"] = round(row[0], 8)
                out["pagerank_pct"] = pct
        except Exception:
            pass
        return out
    finally:
        conn.close()


def refs_of(wid: int, limit: int = 20, offset: int = 0) -> dict:
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
               LIMIT ? OFFSET ?""", (wid, limit, max(0, offset))).fetchall()
        items = [dict(r) for r in rows]
        total = conn.execute("SELECT COUNT(*) FROM work_refs WHERE wid=?", (wid,)).fetchone()[0]
        return {"found": True, "work": w, "items": items, "total": total,
                "offset": max(0, offset),
                "in_corpus": sum(1 for i in items if i.get("ref_wid"))}
    finally:
        conn.close()


def citing_of(wid: int, limit: int = 20, offset: int = 0) -> dict:
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
               ORDER BY w2.cites DESC LIMIT ? OFFSET ?""",
            (wid, limit, max(0, offset))).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM work_refs WHERE ref_wid=?", (wid,)).fetchone()[0]
        return {"found": True, "work": w, "items": [dict(r) for r in rows],
                "total": total, "offset": max(0, offset)}
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


def build_paths(start: int, edges: list[dict], hops: int = 2,
                max_paths: int = 10) -> list[list[int]]:
    """从边集里枚举"被引方向"的路径（纯函数，便于测试）。

    edges 形如 [{"src": 引用方, "dst": 被引方}]：src → dst 表示"src 引用了 dst"。
    从 start 出发沿 dst 向上走，得到 start ← A ← B 这样的链。
    """
    children: dict[int, list[int]] = {}
    for e in edges or []:
        children.setdefault(int(e["dst"]), []).append(int(e["src"]))
    out: list[list[int]] = []

    def walk(node: int, path: list[int]) -> None:
        if len(out) >= max_paths:
            return
        if len(path) - 1 >= hops:
            out.append(list(path))
            return
        nxt = children.get(node) or []
        if not nxt:
            if len(path) > 1:
                out.append(list(path))
            return
        for c in nxt[:5]:                      # 每层最多展开 5 个，防爆炸
            if c in path:
                continue
            walk(c, path + [c])

    walk(start, [start])
    return [p for p in out if len(p) >= 2][:max_paths]


def chain(wid: int, hops: int = 2, max_nodes: int = 40) -> dict:
    """两跳引用链（被引方向，仅库内边）。

    只看库内：`ref_wid IS NULL` 的边无法继续走，链就停在那一层。
    超出 `max_nodes` 时置 `truncated=True`，不静默截断。
    """
    hops = max(1, min(int(hops or 1), 2))
    conn = connect()
    try:
        w = _work(conn, wid)
        if not w:
            return {"found": False, "nodes": [], "edges": [], "paths": [],
                    "truncated": False}
        nodes: dict[int, dict] = {wid: {**w, "depth": 0}}
        edges: list[dict] = []
        frontier = {wid}
        truncated = False
        for depth in range(1, hops + 1):
            if not frontier:
                break
            ph = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"""SELECT r.wid AS citing, r.ref_wid AS cited
                    FROM work_refs r WHERE r.ref_wid IN ({ph})""",
                sorted(frontier)).fetchall()
            nxt: set[int] = set()
            for r in rows:
                citing, cited = int(r["citing"]), int(r["cited"])
                edges.append({"src": citing, "dst": cited, "depth": depth})
                if citing not in nodes:
                    if len(nodes) >= max_nodes:
                        truncated = True
                        continue
                    row = _work(conn, citing)
                    if row:
                        nodes[citing] = {**row, "depth": depth}
                nxt.add(citing)
            frontier = nxt
        seen, uniq = set(), []
        for e in edges:
            k = (e["src"], e["dst"])
            if k not in seen:
                seen.add(k)
                uniq.append(e)
        return {"found": True, "work": w, "nodes": list(nodes.values()),
                "edges": uniq,
                "paths": build_paths(wid, uniq, hops=hops, max_paths=8),
                "truncated": truncated}
    finally:
        conn.close()


def top_cited(limit: int = 10, sort: str = "count") -> list[dict]:
    """库内被引最多的论文。

    sort="count"   按库内引用次数（默认，直观）
    sort="pagerank" 按引用图 PageRank（区分"被重要论文引用"）
    两种口径差异很大，见 docs/m39——稀疏图上 PageRank 会偏向小圈子。
    """
    conn = connect()
    try:
        if sort == "pagerank":
            rows = conn.execute(
                """SELECT w.wid, w.title, w.year, w.cites,
                          r.in_corpus_cited_by AS in_corpus_cites, r.score AS pagerank
                   FROM work_rank r JOIN works w ON w.wid = r.wid
                   ORDER BY r.score DESC LIMIT ?""", (limit,)).fetchall()
        else:
            rows = conn.execute(
                """SELECT w.wid, w.title, w.year, w.cites,
                          COUNT(*) AS in_corpus_cites
                   FROM work_refs r JOIN works w ON w.wid = r.ref_wid
                   GROUP BY w.wid ORDER BY in_corpus_cites DESC, w.cites DESC LIMIT ?""",
                (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
