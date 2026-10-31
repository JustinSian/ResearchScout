"""引用图上的论文聚类：把 11k 篇论文按"互相引用关系"分堆。

为什么不用 Louvain：要额外依赖（networkx/python-louvain），而我们的图只有 11k 节点 / 30k 边，
**标签传播**（label propagation）几秒就能跑完，且实现只有几十行、可完全单测。

做法：把引用边当无向边，每个节点初始是自己的标签，
反复让每个节点取"邻居里出现最多的标签"（并列取最小标签，保证确定性），
直到不再变化。

局限：标签传播容易把一个社区吞掉（大社区吸小社区），所以输出里带 `size`
和"社区内被引最多"的代表论文，便于人工判断这堆是不是真的同一主题。
"""
from __future__ import annotations

import json
import time
from collections import Counter

from app.db import connect, init_db

DDL = """
CREATE TABLE IF NOT EXISTS work_cluster (
  wid        INTEGER PRIMARY KEY,
  cluster_id INTEGER NOT NULL,
  size       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_cluster ON work_cluster(cluster_id);
"""


def label_propagation(nodes: list[int], edges: list[tuple[int, int]],
                      iters: int = 40) -> dict[int, int]:
    """（保留作对照，不用于生产）标签传播：几行就能写完，但**会把小社区吞掉**。

    实测：两个三角形 + 一条桥边 → 合并成 1 个社区。所以生产用 `louvain()`。
    """
    if not nodes:
        return {}
    adj: dict[int, list[int]] = {n: [] for n in nodes}
    nodeset = set(nodes)
    for a, b in edges:
        if a == b or a not in nodeset or b not in nodeset:
            continue
        adj[a].append(b)
        adj[b].append(a)
    label = {n: n for n in nodes}
    order = sorted(nodes)
    for _ in range(iters):
        changed = 0
        for n in order:
            nb = adj[n]
            if not nb:
                continue
            cnt = Counter(label[m] for m in nb)
            best = max(cnt.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if label[n] != best:
                label[n] = best
                changed += 1
        if not changed:
            break
    root: dict[int, int] = {}
    for n in nodes:
        root.setdefault(label[n], n)
    return {n: root[label[n]] for n in nodes}


def louvain(nodes: list[int], edges: list[tuple[int, int]],
            max_passes: int = 20) -> dict[int, int]:
    """Louvain 局部移动阶段（单层）：按模块度增益把节点挪到邻居社区。

    这是标签传播的替换：标签传播在两个连通小团体之间会直接合并，
    Louvain 只在"模块度增益为正"时才移动，桥边不足以把两边并掉。
    返回 {wid: cluster_id}，cluster_id 取社区内最小原始 id（确定性）。
    """
    if not nodes:
        return {}
    adj: dict[int, Counter] = {n: Counter() for n in nodes}
    nodeset = set(nodes)
    for a, b in edges:
        if a == b or a not in nodeset or b not in nodeset:
            continue
        adj[a][b] += 1
        adj[b][a] += 1
    m2 = sum(sum(c.values()) for c in adj.values())   # 2m
    if m2 == 0:
        return {n: n for n in nodes}

    degree = {n: sum(adj[n].values()) for n in nodes}
    comm = {n: n for n in nodes}
    tot = {n: float(degree[n]) for n in nodes}        # 社区内度数之和

    for _ in range(max_passes):
        moved = False
        for n in sorted(nodes):
            ci = comm[n]
            ki = degree[n]
            tot[ci] -= ki
            # 邻居按社区聚合
            w_to: Counter = Counter()
            for nb, w in adj[n].items():
                w_to[comm[nb]] += w
            best_c, best_gain = ci, 0.0
            for c, w in w_to.items():
                # 与 ΔQ 成比例：w - tot[c]*ki/(2m)
                gain = w - tot[c] * ki / m2
                if gain > best_gain or (gain == best_gain and c < best_c):
                    best_c, best_gain = c, gain
            comm[n] = best_c
            tot[best_c] += ki
            if best_c != ci:
                moved = True
        if not moved:
            break

    root: dict[int, int] = {}
    for n in sorted(nodes):
        root.setdefault(comm[n], n)
    return {n: root[comm[n]] for n in nodes}


def build(log=print) -> dict:
    """构建并落库（幂等）。"""
    init_db()
    conn = connect()
    t0 = time.time()
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        nodes = [r[0] for r in conn.execute("SELECT wid FROM works")]
        edges = [(r[0], r[1]) for r in conn.execute(
            "SELECT wid, ref_wid FROM work_refs WHERE ref_wid IS NOT NULL")]
        labels = louvain(nodes, edges)
        sizes = Counter(labels.values())
        conn.execute("DELETE FROM work_cluster")
        conn.executemany(
            "INSERT INTO work_cluster(wid, cluster_id, size) VALUES(?,?,?)",
            [(w, c, sizes[c]) for w, c in labels.items()])
        conn.commit()
        largest = sizes.most_common(1)[0] if sizes else (None, 0)
        stats = {"nodes": len(nodes), "edges": len(edges),
                 "clusters": len(sizes),
                 "largest_cluster": largest[0], "largest_size": largest[1],
                 "seconds": round(time.time() - t0, 2)}
        conn.execute(
            "INSERT INTO meta(k, v) VALUES('work_cluster', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(stats, ensure_ascii=False),))
        conn.commit()
        log(f"  论文聚类：{stats['nodes']} 篇 → {stats['clusters']} 个社区 / "
            f"最大 {stats['largest_size']} 篇 / {stats['seconds']}s")
        return stats
    finally:
        conn.close()


def top_clusters(limit: int = 10, per_cluster: int = 3) -> list[dict]:
    """最大的若干个社区，附代表论文（社区内被库内引用最多）。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT cluster_id, COUNT(*) AS size FROM work_cluster
               GROUP BY cluster_id ORDER BY size DESC LIMIT ?""", (limit,)).fetchall()
        out = []
        for r in rows:
            cid, size = int(r[0]), int(r[1])
            papers = conn.execute(
                """SELECT w.wid, w.title, w.year,
                          (SELECT COUNT(*) FROM work_refs x WHERE x.ref_wid = w.wid) AS cited
                   FROM work_cluster c JOIN works w ON w.wid = c.wid
                   WHERE c.cluster_id = ?
                   ORDER BY cited DESC, w.cites DESC LIMIT ?""", (cid, per_cluster)).fetchall()
            out.append({"cluster_id": cid, "size": size,
                        "papers": [dict(p) for p in papers]})
        return out
    finally:
        conn.close()


if __name__ == "__main__":
    build()
