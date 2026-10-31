"""引用图上的论文聚类：把 11k 篇论文按"互相引用关系"分堆。

算法：**Louvain 局部移动**（按模块度增益移动节点）。
一开始用标签传播，但它在"两个团 + 一条桥边"上会把两个团合并（见 docs/m41），
所以生产用 Louvain，标签传播只保留作对照实现并钉在测试里。

额外能力：**桥接检测**——社区里存在"去掉它社区就断成几块"的论文
（articulation point），这类论文通常就是跨领域的方法/综述，能把两个圈子粘在一起。
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


def articulation_points(nodes: list[int], edges: list[tuple[int, int]]) -> set[int]:
    """Tarjan 割点：去掉它会让连通块变多的节点（纯函数，确定性）。

    用途：社区里"桥接两个子圈子"的论文，通常就是跨领域方法/综述。
    """
    if not nodes:
        return set()
    nodeset = set(nodes)
    adj: dict[int, set[int]] = {n: set() for n in nodes}
    for a, b in edges:
        if a == b or a not in nodeset or b not in nodeset:
            continue
        adj[a].add(b)
        adj[b].add(a)

    disc: dict[int, int] = {}
    low: dict[int, int] = {}
    parent: dict[int, int | None] = {}
    aps: set[int] = set()
    timer = 0

    for root in sorted(nodes):
        if root in disc:
            continue
        # 迭代 DFS，避免深链递归爆栈
        stack: list[tuple[int, int]] = [(root, 0)]
        children_of_root = 0
        disc[root] = low[root] = timer
        timer += 1
        parent[root] = None
        while stack:
            node, idx = stack.pop()
            neighbors = sorted(adj[node])
            if idx < len(neighbors):
                stack.append((node, idx + 1))
                nb = neighbors[idx]
                if nb not in disc:
                    parent[nb] = node
                    disc[nb] = low[nb] = timer
                    timer += 1
                    if node == root:
                        children_of_root += 1
                    stack.append((nb, 0))
                elif nb != parent.get(node):
                    low[node] = min(low[node], disc[nb])
            else:
                p = parent.get(node)
                if p is not None:
                    low[p] = min(low[p], low[node])
                    if p != root and low[node] >= disc[p]:
                        aps.add(p)
        if children_of_root > 1:
            aps.add(root)
    return aps


def components_after_removal(nodes: list[int], edges: list[tuple[int, int]],
                             removed: int) -> list[int]:
    """去掉 `removed` 之后的连通块大小列表（降序）。"""
    rest = [n for n in nodes if n != removed]
    restset = set(rest)
    adj: dict[int, list[int]] = {n: [] for n in rest}
    for a, b in edges:
        if a == removed or b == removed:
            continue
        if a in restset and b in restset:
            adj[a].append(b)
            adj[b].append(a)
    seen: set[int] = set()
    sizes: list[int] = []
    for n in rest:
        if n in seen:
            continue
        stack = [n]
        seen.add(n)
        cnt = 0
        while stack:
            cur = stack.pop()
            cnt += 1
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        sizes.append(cnt)
    return sorted(sizes, reverse=True)


def bridges(cluster_id: int, limit: int = 5) -> list[dict]:
    """某个社区里的桥接论文：去掉后社区分裂得最厉害的若干个。"""
    conn = connect()
    try:
        members = [r[0] for r in conn.execute(
            "SELECT wid FROM work_cluster WHERE cluster_id=?", (cluster_id,))]
        if not members:
            return []
        ph = ",".join("?" * len(members))
        edges = [(r[0], r[1]) for r in conn.execute(
            f"""SELECT wid, ref_wid FROM work_refs
                WHERE ref_wid IN ({ph}) AND wid IN ({ph})""",
            (*members, *members))]
        aps = articulation_points(members, edges)
        out = []
        for wid in sorted(aps):
            sizes = components_after_removal(members, edges, wid)
            if len(sizes) < 2:
                continue
            row = conn.execute(
                "SELECT title, year FROM works WHERE wid=?", (wid,)).fetchone()
            total = sum(sizes)
            out.append({"wid": wid, "title": (row[0] if row else ""),
                        "year": (row[1] if row else None),
                        "parts": len(sizes),
                        "separated": total - sizes[0],
                        "largest_share": round(sizes[0] / max(total, 1), 3)})
        # 先看"被拆成几块"，再看"有多少节点被隔离出主块"
        out.sort(key=lambda x: (-x["parts"], -x["separated"]))
        return out[:limit]
    finally:
        conn.close()


def bridge_status(wid: int) -> dict:
    """这篇论文是不是所在社区的桥接点，以及去掉它社区会断成几块。"""
    conn = connect()
    try:
        row = conn.execute(
            "SELECT cluster_id FROM work_cluster WHERE wid=?", (wid,)).fetchone()
        if not row:
            return {"is_bridge": False}
        cid = int(row[0])
        for b in bridges(cid, limit=200):
            if b["wid"] == wid:
                return {"is_bridge": True, "cluster_id": cid,
                        "parts": b["parts"], "largest_share": b["largest_share"]}
        return {"is_bridge": False, "cluster_id": cid}
    finally:
        conn.close()


def edge_betweenness(nodes: list[int],
                     edges: list[tuple[int, int]]) -> dict[tuple[int, int], float]:
    """边介数（Brandes 算法，无向、迭代实现）。

    为什么割点不够：割点只能抓"去掉单个节点就断开"的情形。
    如果两个子圈子由 A-B 和 C-D **两条路径**相连，去掉任何一个点都不断开，
    但这两条边承载了全部跨圈流量——边介数能抓出来。

    返回 {(min_u, max_u): 分数}，无向边只记一次。
    """
    if not nodes:
        return {}
    nodeset = set(nodes)
    adj: dict[int, list[int]] = {n: [] for n in nodes}
    pairs: set[tuple[int, int]] = set()
    for a, b in edges:
        if a == b or a not in nodeset or b not in nodeset:
            continue
        key = (min(a, b), max(a, b))
        if key in pairs:
            continue
        pairs.add(key)
        adj[a].append(b)
        adj[b].append(a)

    score: dict[tuple[int, int], float] = {k: 0.0 for k in pairs}
    from collections import deque

    for s in nodes:
        stack: list[int] = []
        pred: dict[int, list[int]] = {n: [] for n in nodes}
        sigma: dict[int, float] = {n: 0.0 for n in nodes}
        dist: dict[int, int] = {n: -1 for n in nodes}
        sigma[s] = 1.0
        dist[s] = 0
        q = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta: dict[int, float] = {n: 0.0 for n in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    c = sigma[v] / sigma[w] * (1.0 + delta[w])
                    score[(min(v, w), max(v, w))] += c
                    delta[v] += c
    # 无向图每条边被两个源点各算一次
    return {k: v / 2.0 for k, v in score.items()}


def edge_bridges(cluster_id: int, limit: int = 5) -> list[dict]:
    """社区里"承载跨圈流量最多"的边（边介数 Top N）。"""
    conn = connect()
    try:
        members = [r[0] for r in conn.execute(
            "SELECT wid FROM work_cluster WHERE cluster_id=?", (cluster_id,))]
        if not members:
            return []
        ph = ",".join("?" * len(members))
        edges = [(r[0], r[1]) for r in conn.execute(
            f"""SELECT wid, ref_wid FROM work_refs
                WHERE ref_wid IN ({ph}) AND wid IN ({ph})""",
            (*members, *members))]
        if not edges:
            return []
        scores = edge_betweenness(members, edges)
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
        titles: dict[int, str] = {}
        for wid in {w for k in scores for w in k}:
            row = conn.execute("SELECT title FROM works WHERE wid=?", (wid,)).fetchone()
            titles[wid] = row[0] if row else ""
        return [{"a": k[0], "b": k[1], "score": round(v, 3),
                 "title_a": titles.get(k[0], ""), "title_b": titles.get(k[1], "")}
                for k, v in top]
    finally:
        conn.close()
