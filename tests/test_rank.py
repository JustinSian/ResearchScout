"""引用图 PageRank 的数学性质测试（纯函数，不碰数据库）。

重点不是"某个数等于多少"，而是性质：
- 分数归一（sum≈1）；
- 被引用者分数更高；
- 悬空节点不泄漏权重；
- 自环被忽略（否则会自我放大）；
- 空图不炸。
"""
from __future__ import annotations


def test_sums_to_one():
    from app.rank import pagerank
    s = pagerank([1, 2, 3], [(2, 1), (3, 2)])
    assert abs(sum(s.values()) - 1.0) < 1e-9


def test_cited_beats_citing():
    from app.rank import pagerank
    # 2、3 都引用 1 → 1 分数最高；2、3 对称
    s = pagerank([1, 2, 3], [(2, 1), (3, 1)])
    assert s[1] > s[2] and s[1] > s[3]
    assert abs(s[2] - s[3]) < 1e-9


def test_chain_order():
    from app.rank import pagerank
    # 1 ← 2 ← 3：1 最"基础"，3 是源头
    s = pagerank([1, 2, 3], [(2, 1), (3, 2)])
    assert s[1] > s[2] > s[3]


def test_dangling_does_not_leak():
    from app.rank import pagerank
    # 4 没有出边（悬空），分数应仍归一
    s = pagerank([1, 2, 3, 4], [(2, 1), (3, 1)])
    assert abs(sum(s.values()) - 1.0) < 1e-9
    assert all(v > 0 for v in s.values())


def test_self_loop_ignored():
    from app.rank import pagerank
    s = pagerank([1, 2], [(1, 1), (1, 2)])
    assert abs(sum(s.values()) - 1.0) < 1e-9
    # 自环不该把 1 抬到不合理的高位
    assert s[1] < 0.9


def test_empty_graph():
    from app.rank import pagerank
    assert pagerank([], []) == {}
    assert pagerank([1], []) == {1: 1.0} or abs(sum(pagerank([1], []).values()) - 1.0) < 1e-9


def test_unknown_nodes_in_edges_are_skipped():
    from app.rank import pagerank
    # 边里出现不在 nodes 里的 id：忽略而不是抛异常
    s = pagerank([1, 2], [(99, 1), (2, 1)])
    assert abs(sum(s.values()) - 1.0) < 1e-9
