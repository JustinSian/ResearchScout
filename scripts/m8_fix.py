"""1) 用真实存在的边测 shortest_path 是否坏了 2) 用 Louvain 重算社区并落库。"""
from __future__ import annotations

import networkx as nx

from app.db import connect
from app.graph_analytics import DDL, shortest_path


def test_paths() -> None:
    conn = connect()
    edges = [dict(r) for r in conn.execute(
        "SELECT a,b,weight FROM edges WHERE kind='coauthor' ORDER BY weight DESC LIMIT 3")]
    print("== 用真实边测路径 ==")
    for e in edges:
        r = shortest_path(int(e["a"]), int(e["b"]))
        print(f"  边 {e['a']}-{e['b']} (权重 {e['weight']}) → hops={r['hops']} path={r['path']}")
    # 2 跳：a-c 与 c-b 都存在
    a = int(edges[0]["a"]); c = int(edges[0]["b"])
    row = conn.execute("SELECT CASE WHEN a=? THEN b ELSE a END AS o FROM edges "
                       "WHERE kind='coauthor' AND (a=? OR b=?) AND NOT (a=? AND b=?) "
                       "AND NOT (a=? AND b=?) LIMIT 1", (c, c, c, a, c, c, a)).fetchone()
    if row:
        r = shortest_path(a, int(row["o"]), max_depth=4)
        print(f"  2 跳 {a}→{row['o']} → hops={r['hops']} path={r['path']}")
    conn.close()


def rebuild_louvain() -> None:
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    g = nx.Graph()
    for r in conn.execute("SELECT a,b,weight FROM edges WHERE kind='coauthor'"):
        g.add_edge(int(r["a"]), int(r["b"]), weight=int(r["weight"] or 1))
    comms = nx.community.louvain_communities(g, weight="weight", seed=7, resolution=1.0)
    comms.sort(key=len, reverse=True)
    conn.execute("DELETE FROM person_community")
    rows = []
    for c, members in enumerate(comms):
        for pid in members:
            rows.append((int(pid), c, len(members)))
    conn.executemany("INSERT OR REPLACE INTO person_community(pid,community,csize) "
                     "VALUES(?,?,?)", rows)
    conn.commit()
    sizes = [len(c) for c in comms]
    print(f"== Louvain ==\n  社区 {len(comms)}，前 8 {sizes[:8]}，"
          f"模块度 {nx.community.modularity(g, comms):.3f}，写入 {len(rows)} 人")
    # 抽查：Bengio 的社区
    for pid, nm in ((122, "Bengio"), (17, "Hinton"), (176, "Stecher")):
        r = conn.execute("SELECT community,csize FROM person_community WHERE pid=?",
                         (pid,)).fetchone()
        print(f"  {nm}: 社区 {r['community']} 规模 {r['csize']}" if r else f"  {nm}: 无社区")
    conn.close()


if __name__ == "__main__":
    test_paths()
    rebuild_louvain()
