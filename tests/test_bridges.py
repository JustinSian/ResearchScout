"""桥接检测（割点）的纯函数测试。

割点 = 去掉它之后连通块变多的节点。在引用图里，这类论文通常就是
"把两个子圈子粘在一起"的跨领域方法/综述。
"""
from __future__ import annotations


def test_path_hubs_are_articulation_points():
    from app.cluster import articulation_points
    assert articulation_points([1, 2, 3, 4], [(1, 2), (2, 3), (3, 4)]) == {2, 3}


def test_triangle_has_none():
    from app.cluster import articulation_points
    assert articulation_points([1, 2, 3], [(1, 2), (2, 3), (1, 3)]) == set()


def test_single_bridge_edge_endpoints():
    """两个三角形 + 一条桥：只有桥两端的两点是割点。"""
    from app.cluster import articulation_points
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, 4)]
    assert articulation_points(nodes, edges) == {3, 4}


def test_star_center_is_articulation_point():
    from app.cluster import articulation_points
    nodes = [0, 1, 2, 3]
    edges = [(0, 1), (0, 2), (0, 3)]
    assert articulation_points(nodes, edges) == {0}


def test_disconnected_parts_have_no_extra_aps():
    from app.cluster import articulation_points
    # 两个独立三角形：各自没有割点
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6)]
    assert articulation_points(nodes, edges) == set()


def test_components_after_removal_bridge():
    from app.cluster import components_after_removal
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, 4)]
    assert components_after_removal(nodes, edges, 3) == [3, 2]


def test_components_after_removal_non_articulation():
    from app.cluster import components_after_removal
    nodes = [1, 2, 3]
    edges = [(1, 2), (2, 3), (1, 3)]
    assert components_after_removal(nodes, edges, 1) == [2]


def test_empty_and_single():
    from app.cluster import articulation_points, components_after_removal
    assert articulation_points([], []) == set()
    assert articulation_points([7], []) == set()
    assert components_after_removal([7], [], 7) == []


def test_deterministic():
    from app.cluster import articulation_points
    nodes = list(range(1, 12))
    edges = [(i, i + 1) for i in range(1, 11)]
    assert articulation_points(nodes, edges) == articulation_points(nodes, edges)
