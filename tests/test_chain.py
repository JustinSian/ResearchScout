"""引用链路径枚举（纯函数）：不碰数据库，只验证图遍历语义。

边方向：`src → dst` 表示"src 引用了 dst"。从 start 出发沿 dst→src 向上走，
得到 start ← A ← B（两跳）。
"""
from __future__ import annotations


def test_single_hop():
    from app.citations import build_paths
    edges = [{"src": 2, "dst": 1}]
    assert build_paths(1, edges, hops=1) == [[1, 2]]


def test_two_hops():
    from app.citations import build_paths
    edges = [{"src": 2, "dst": 1}, {"src": 3, "dst": 2}]
    paths = build_paths(1, edges, hops=2)
    assert [1, 2, 3] in paths


def test_hops_limit_stops_early():
    from app.citations import build_paths
    edges = [{"src": 2, "dst": 1}, {"src": 3, "dst": 2}]
    paths = build_paths(1, edges, hops=1)
    assert all(len(p) <= 2 for p in paths)
    assert [1, 2] in paths


def test_no_edges_returns_empty():
    from app.citations import build_paths
    assert build_paths(1, [], hops=2) == []


def test_cycle_does_not_loop():
    """成环时不能死循环；同时不应输出重复节点的"路径"。

    1←2←1 这种环里没有合法的两跳路径（第二步会回到起点），
    所以允许返回空列表——关键是**终止**且不产生重复节点。
    """
    from app.citations import build_paths
    edges = [{"src": 2, "dst": 1}, {"src": 1, "dst": 2}]
    paths = build_paths(1, edges, hops=2)
    for p in paths:
        assert len(set(p)) == len(p)      # 不重复节点
    assert isinstance(paths, list)        # 不抛异常、不死循环


def test_max_paths_cap():
    from app.citations import build_paths
    edges = [{"src": i, "dst": 1} for i in range(2, 30)]
    paths = build_paths(1, edges, hops=1, max_paths=3)
    assert len(paths) == 3
