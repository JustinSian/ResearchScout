"""图分析：社区划分 / 最短合作路径 / 机构关系网。

为什么不上 Neo4j：
- 这台机器没有 docker、没有 sudo，装不了也跑不起；
- 现有需求（1~2 跳邻居、路径、机构聚合）在 SQLite 上都能做，
  88,558 条边全量读进内存只有几十 MB；
- 真正的短板不是"图存储"，而是"图上没有分析"——社区、中心度、路径才是。
"""
from __future__ import annotations

import json
import random
import time

from app.db import connect

DDL = ["""CREATE TABLE IF NOT EXISTS person_community (
    pid INTEGER PRIMARY KEY,
    community INTEGER NOT NULL,
    csize INTEGER NOT NULL
)""", "CREATE INDEX IF NOT EXISTS idx_comm ON person_community(community)"]


def _adjacency(conn) -> dict[int, dict[int, int]]:
    adj: dict[int, dict[int, int]] = {}
    for r in conn.execute("SELECT a, b, weight FROM edges WHERE kind='coauthor'"):
        a, b, w = int(r["a"]), int(r["b"]), int(r["weight"] or 1)
        adj.setdefault(a, {})[b] = w
        adj.setdefault(b, {})[a] = w
    return adj


def build_communities(iters: int = 8, log=print) -> dict:
    """标签传播（Label Propagation）：每个点取邻居里权重和最大的标签。

    选它而不是 Louvain：几十行就能写完、无需依赖、结果可复现；
    代价是分辨率不可调（社区偏大），对这个规模的演示够用。
    """
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    adj = _adjacency(conn)
    log(f"  节点 {len(adj)}，边 {sum(len(v) for v in adj.values())//2}")
    labels = {n: n for n in adj}
    nodes = list(adj)
    rng = random.Random(20261029)
    for it in range(iters):
        rng.shuffle(nodes)
        changed = 0
        for n in nodes:
            score: dict[int, float] = {}
            for nb, w in adj[n].items():
                score[labels[nb]] = score.get(labels[nb], 0.0) + w
            if not score:
                continue
            best = max(score.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if labels[n] != best:
                labels[n] = best
                changed += 1
        log(f"    迭代 {it+1}: 变更 {changed}")
        if changed == 0:
            break

    # 归一化成 0..K-1，并计算社区规模
    remap: dict[int, int] = {}
    members: dict[int, list[int]] = {}
    for n, lb in labels.items():
        c = remap.setdefault(lb, len(remap))
        members.setdefault(c, []).append(n)
    big = sorted(members.items(), key=lambda kv: -len(kv[1]))
    conn.execute("DELETE FROM person_community")
    for c, pids in big:
        for pid in pids:
            conn.execute("INSERT OR REPLACE INTO person_community(pid,community,csize) "
                         "VALUES(?,?,?)", (pid, c, len(pids)))
    conn.commit()
    sizes = [len(v) for _c, v in big]
    conn.close()
    stats = {"nodes": len(adj), "communities": len(big),
             "largest": sizes[0] if sizes else 0,
             "top5": sizes[:5],
             "singletons": sum(1 for s in sizes if s == 1)}
    log(f"  社区 {stats['communities']} 个，最大 {stats['largest']}，"
        f"前 5 {stats['top5']}，孤立点 {stats['singletons']}")
    return stats


def community_of(pid: int) -> dict:
    conn = connect()
    try:
        r = conn.execute("SELECT community, csize FROM person_community WHERE pid=?",
                         (pid,)).fetchone()
        return dict(r) if r else {"community": None, "csize": 0}
    finally:
        conn.close()


def shortest_path(a: int, b: int, max_depth: int = 4) -> dict:
    """双向 BFS 找最短合作链（最多 4 跳）。"""
    conn = connect()
    try:
        adj: dict[int, set[int]] = {}

        def nbrs(n: int) -> set[int]:
            if n not in adj:
                rows = conn.execute(
                    "SELECT CASE WHEN a=? THEN b ELSE a END AS o FROM edges "
                    "WHERE kind='coauthor' AND (a=? OR b=?)", (n, n, n)).fetchall()
                adj[n] = {int(r["o"]) for r in rows}
            return adj[n]

        if a == b:
            return {"path": [a], "hops": 0}
        front_a, front_b = {a}, {b}
        seen_a, seen_b = {a: None}, {b: None}
        depth = 0
        while front_a and front_b and depth < max_depth:
            depth += 1
            if len(front_a) > len(front_b):
                front_a, front_b = front_b, front_a
                seen_a, seen_b = seen_b, seen_a
            nxt = set()
            hit = None
            for n in front_a:
                for m in nbrs(n):
                    if m in seen_a:
                        continue
                    seen_a[m] = n
                    if m in seen_b:
                        hit = m
                        nxt.add(m)
                        break
                    nxt.add(m)
                if hit:
                    break
            if hit:
                def chain(node: int, seen: dict, reverse: bool) -> list[int]:
                    out = [node]
                    while seen[out[-1]] is not None:
                        out.append(seen[out[-1]])
                    return out[::-1] if reverse else out
                left = chain(hit, seen_a, reverse=False)      # a -> hit
                right = chain(hit, seen_b, reverse=True)      # hit -> b
                path = left + right[1:]
                return {"path": path, "hops": len(path) - 1}
            front_a = nxt
        return {"path": [], "hops": None}
    finally:
        conn.close()


def institution_graph(top: int = 36) -> dict:
    """机构级关系网：节点=机构，边=两机构之间共同署名的论文数。"""
    conn = connect()
    try:
        inst_of = {int(r["pid"]): r["last_inst"] for r in conn.execute(
            "SELECT pid, last_inst FROM people WHERE status='published' AND last_inst IS NOT NULL")}
        # 一次全读，避免 8000 次子查询（第一版就是这么写的，慢 20 倍）
        by_work: dict[int, list[int]] = {}
        for r in conn.execute("SELECT wid, pid FROM work_authors"):
            by_work.setdefault(int(r["wid"]), []).append(int(r["pid"]))

        counts: dict[str, int] = {}
        for pids in by_work.values():
            for i in {inst_of[p] for p in pids if p in inst_of}:
                counts[i] = counts.get(i, 0) + 1
        keep = {k for k, _v in sorted(counts.items(), key=lambda kv: -kv[1])[:top]}

        pairs: dict[tuple[str, str], int] = {}
        for pids in by_work.values():
            insts = sorted({inst_of[p] for p in pids if p in inst_of and inst_of[p] in keep})
            for i in range(len(insts)):
                for j in range(i + 1, len(insts)):
                    key = (insts[i], insts[j])
                    pairs[key] = pairs.get(key, 0) + 1
        size = {k: v for k, v in counts.items() if k in keep}
        nodes = [{"name": k, "experts": size[k]} for k in keep]
        edges = [{"a": a, "b": b, "weight": w} for (a, b), w in
                 sorted(pairs.items(), key=lambda kv: -kv[1])[:220]]
        return {"nodes": nodes, "edges": edges, "top": top}
    finally:
        conn.close()


if __name__ == "__main__":
    print(build_communities(log=lambda s: print(s, flush=True)))
