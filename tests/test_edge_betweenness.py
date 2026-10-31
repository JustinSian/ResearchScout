"""边介数（Brandes）的纯函数测试。

用途：割点只能抓"去掉单点就断开"，抓不到"两条路径相连"的桥；
边介数衡量"有多少最短路径经过这条边"，能抓到那种情形。
"""
from __future__ import annotations


def test_path_middle_edge_wins():
    from app.cluster import edge_betweenness
    s = edge_betweenness([1, 2, 3, 4], [(1, 2), (2, 3), (3, 4)])
    assert max(s, key=lambda k: s[k]) == (2, 3)
    assert s[(1, 2)] == s[(3, 4)]          # 对称


def test_triangle_is_symmetric():
    from app.cluster import edge_betweenness
    s = edge_betweenness([1, 2, 3], [(1, 2), (2, 3), (1, 3)])
    vals = list(s.values())
    assert len(set(round(v, 6) for v in vals)) == 1


def test_two_paths_are_symmetric():
    """1-2-3 与 1-4-3 两条路径并联：四条边承载相同流量。"""
    from app.cluster import edge_betweenness
    s = edge_betweenness([1, 2, 3, 4], [(1, 2), (2, 3), (1, 4), (4, 3)])
    vals = [round(v, 6) for v in s.values()]
    assert len(set(vals)) == 1


def test_bridge_edge_wins():
    """两个三角形由一条边相连：桥边的介数最高（所有跨团流量都走它）。

    注意不能用"三角形 + 一条弦"来测：三角形三条边互相对称，介数必然相等。
    """
    from app.cluster import edge_betweenness
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, 4)]
    s = edge_betweenness(nodes, edges)
    assert max(s, key=lambda k: s[k]) == (3, 4)


def test_isolated_edge_unit_betweenness():
    """一条孤立边：它的两个端点之间只有这一条路，介数为 1（不是 0）。"""
    from app.cluster import edge_betweenness
    s = edge_betweenness([1, 2, 3, 4], [(1, 2), (3, 4)])
    assert s[(1, 2)] == 1.0 and s[(3, 4)] == 1.0


def test_parallel_edges_deduped():
    from app.cluster import edge_betweenness
    a = edge_betweenness([1, 2], [(1, 2)])
    b = edge_betweenness([1, 2], [(1, 2), (2, 1), (1, 2)])
    assert a == b


def test_empty_and_single():
    from app.cluster import edge_betweenness
    assert edge_betweenness([], []) == {}
    assert edge_betweenness([1], []) == {}


def test_deterministic():
    from app.cluster import edge_betweenness
    nodes = list(range(1, 8))
    edges = [(i, i + 1) for i in range(1, 7)] + [(1, 7)]
    assert edge_betweenness(nodes, edges) == edge_betweenness(nodes, edges)
