"""引用图社区检测（Louvain 局部移动）的性质测试（纯函数）。

重点：
- 两个团 + 一条桥边，不能合并（这正是标签传播踩的坑）；
- 断开的两个团必须分开；
- 孤立点自成一类；
- 结果是确定性的（同输入同输出），否则每次重建社区 id 都会变。
"""
from __future__ import annotations


def test_bridge_does_not_merge_cliques():
    """两个三角形 + 一条桥边 → 2 个社区（标签传播会并成 1 个）。"""
    from app.cluster import louvain
    from collections import Counter
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, 4)]
    groups = Counter(louvain(nodes, edges).values())
    assert len(groups) == 2
    assert sorted(groups.values()) == [3, 3]


def test_label_propagation_would_merge_this():
    """把对照实现的失败记录下来：同一张图它会并成 1 个社区。"""
    from app.cluster import label_propagation
    from collections import Counter
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6), (3, 4)]
    assert len(Counter(label_propagation(nodes, edges).values())) == 1


def test_disconnected_cliques():
    from app.cluster import louvain
    from collections import Counter
    nodes = [1, 2, 3, 4, 5, 6]
    edges = [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6)]
    groups = Counter(louvain(nodes, edges).values())
    assert len(groups) == 2


def test_isolated_node_is_own_cluster():
    from app.cluster import louvain
    labels = louvain([1, 2, 3, 99], [(1, 2), (2, 3)])
    assert labels[99] == 99


def test_empty_and_single():
    from app.cluster import louvain
    assert louvain([], []) == {}
    assert louvain([7], []) == {7: 7}


def test_deterministic():
    from app.cluster import louvain
    nodes = list(range(1, 21))
    edges = [(i, i + 1) for i in range(1, 10)] + [(i, i + 1) for i in range(11, 20)]
    a = louvain(nodes, edges)
    b = louvain(nodes, edges)
    assert a == b


def test_edges_with_unknown_nodes_ignored():
    from app.cluster import louvain
    labels = louvain([1, 2], [(1, 2), (1, 999)])
    assert set(labels) == {1, 2}
