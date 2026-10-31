"""引用图 PageRank：区分"被重要论文引用"和"被一堆水文引用"。

为什么直接计数不够：
- `in_corpus_cites` 把每条引用当等权。一个被综述顺手带一句的工作，
  和被 Nature 正主引用的工作，计数一样；
- 引用网络本身有结构：被"高被引且被高被引者引用"的论文，才是真正的基石。

实现：库内引用图（约 11k 节点 / 30k 边）上跑阻尼 0.85 的幂迭代，
悬空节点（无人引用）按均匀分布分摊。结果写 `work_rank`。

注意：只覆盖库内 5.3% 的引用边，所以 PageRank 是**库内相对重要性**，
不是"学术地位"。
"""
from __future__ import annotations

import json
import time

from app.db import connect, init_db

DDL = """
CREATE TABLE IF NOT EXISTS work_rank (
  wid     INTEGER PRIMARY KEY,
  score   REAL NOT NULL,
  in_corpus_cited_by INTEGER NOT NULL,
  score_recent REAL
);
CREATE INDEX IF NOT EXISTS idx_work_rank ON work_rank(score DESC);
"""

MIGRATE = ["ALTER TABLE work_rank ADD COLUMN score_recent REAL"]


def pagerank(nodes: list[int], edges: list[tuple[int, int]],
             damping: float = 0.85, iters: int = 40,
             weights: dict[int, float] | None = None) -> dict[int, float]:
    """纯函数版 PageRank：edges 为 (src, dst)，表示 src 引用了 dst。

    dst 获得分数。悬空节点（没有出边）把权重均分给全图，避免分数泄漏。
    `weights` 是"随机跳转"的偏好分布（用于时间衰减：新论文权重更高）。
    """
    if not nodes:
        return {}
    n = len(nodes)
    idx = {w: i for i, w in enumerate(nodes)}
    if weights:
        wsum = sum(max(weights.get(w, 0.0), 1e-9) for w in nodes) or 1.0
        reset = [max(weights.get(w, 0.0), 1e-9) / wsum for w in nodes]
    else:
        reset = [1.0 / n] * n
    out_deg = [0] * n
    incoming: list[list[int]] = [[] for _ in range(n)]
    for src, dst in edges:
        i, j = idx.get(src), idx.get(dst)
        if i is None or j is None or i == j:
            continue
        out_deg[i] += 1
        incoming[j].append(i)
    score = list(reset)
    for _ in range(iters):
        new = [(1.0 - damping) * reset[i] for i in range(n)]
        dangling = sum(score[i] for i in range(n) if out_deg[i] == 0)
        if dangling:
            for i in range(n):
                new[i] += damping * dangling * reset[i]
        for j in range(n):
            if not incoming[j]:
                continue
            s = 0.0
            for i in incoming[j]:
                s += score[i] / out_deg[i]
            new[j] += damping * s
        score = new
    total = sum(score) or 1.0
    return {w: score[idx[w]] / total for w in nodes}


def build(log=print) -> dict:
    """从 work_refs 构建图并落库（幂等）。"""
    init_db()
    conn = connect()
    t0 = time.time()
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        for stmt in MIGRATE:            # 老库补列（已存在会抛异常，忽略）
            try:
                conn.execute(stmt)
            except Exception:
                pass
        nodes = [r[0] for r in conn.execute("SELECT wid FROM works")]
        edges = [(r[0], r[1]) for r in conn.execute(
            "SELECT wid, ref_wid FROM work_refs WHERE ref_wid IS NOT NULL")]
        cited_by = {r[0]: r[1] for r in conn.execute(
            "SELECT ref_wid, COUNT(*) FROM work_refs WHERE ref_wid IS NOT NULL "
            "GROUP BY ref_wid")}

        # 时间衰减：随机跳转按"半衰期 8 年"偏向新论文。
        # 老论文在纯 PageRank 里天然占优（被引历史长），衰减版给近作加权。
        years = {r[0]: r[1] for r in conn.execute("SELECT wid, year FROM works")}
        known = [y for y in years.values() if isinstance(y, int) and y > 1800]
        newest = max(known) if known else 2025
        tau = 8.0
        weights: dict[int, float] = {}
        for w in nodes:
            y = years.get(w)
            if not isinstance(y, int) or y <= 1800:
                weights[w] = 0.5 ** ((newest - (newest - 12)) / tau)   # 年份缺失按 12 年前算
            else:
                weights[w] = 0.5 ** (max(0, newest - y) / tau)

        scores = pagerank(nodes, edges)
        recent = pagerank(nodes, edges, weights=weights)
        conn.execute("DELETE FROM work_rank")
        conn.executemany(
            "INSERT INTO work_rank(wid, score, in_corpus_cited_by, score_recent) "
            "VALUES(?,?,?,?)",
            [(w, round(s, 12), int(cited_by.get(w, 0)), round(recent.get(w, 0.0), 12))
             for w, s in scores.items()])
        conn.commit()
        top = conn.execute(
            "SELECT w.wid, w.title, r.score, r.in_corpus_cited_by FROM work_rank r "
            "JOIN works w ON w.wid=r.wid ORDER BY r.score DESC LIMIT 1").fetchone()
        top_recent = conn.execute(
            "SELECT w.title, r.score_recent FROM work_rank r JOIN works w ON w.wid=r.wid "
            "ORDER BY r.score_recent DESC LIMIT 1").fetchone()
        stats = {"nodes": len(nodes), "edges": len(edges),
                 "top_wid": top[0] if top else None,
                 "top_title": (top[1] or "")[:80] if top else "",
                 "top_score": round(top[2], 6) if top else 0,
                 "top_recent_title": (top_recent[0] or "")[:80] if top_recent else "",
                 "top_recent_score": round(top_recent[1], 6) if top_recent else 0,
                 "seconds": round(time.time() - t0, 2)}
        conn.execute(
            "INSERT INTO meta(k, v) VALUES('work_rank', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(stats, ensure_ascii=False),))
        conn.commit()
        log(f"  PageRank：{stats['nodes']} 节点 / {stats['edges']} 边 / "
            f"{stats['seconds']}s；最高分《{stats['top_title']}》（{stats['top_score']}）")
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    build()
