"""诊断：1) networkx 是否可用（Louvain） 2) 最短路径为什么没找到 3) 图的连通性。"""
from __future__ import annotations

from app.db import connect


def main() -> None:
    try:
        import networkx as nx
        print("networkx:", nx.__version__)
    except Exception as exc:
        print("networkx 缺失:", type(exc).__name__)
        nx = None

    conn = connect()
    adj: dict[int, set[int]] = {}
    for r in conn.execute("SELECT a,b FROM edges WHERE kind='coauthor'"):
        a, b = int(r["a"]), int(r["b"])
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    print(f"节点 {len(adj)} 边 {sum(len(v) for v in adj.values())//2}")

    deg = sorted((len(v) for v in adj.values()), reverse=True)
    print("度分布 top10:", deg[:10], " 度=1 的点:", sum(1 for d in deg if d == 1))

    if nx is not None:
        g = nx.Graph()
        for a, ns in adj.items():
            for b in ns:
                g.add_edge(a, b)
        comps = sorted((len(c) for c in nx.connected_components(g)), reverse=True)
        print(f"连通分量 {len(comps)} 个，最大 {comps[:5]}")
        comms = nx.community.louvain_communities(g, weight=None, seed=7)
        sizes = sorted((len(c) for c in comms), reverse=True)
        print(f"Louvain 社区 {len(comms)} 个，前 8 {sizes[:8]}，"
              f"模块度 {nx.community.modularity(g, comms):.3f}")

    # 路径抽查
    from app.graph_analytics import shortest_path
    for a, b, label in ((122, 15, "Bengio-Glorot"), (122, 17, "Bengio-Hinton"),
                        (77, 176, "Vanderplas-Stecher")):
        r = shortest_path(a, b, max_depth=4)
        print(f"  路径 {label}: hops={r['hops']} path={r['path']}")
    conn.close()


if __name__ == "__main__":
    main()
