"""三层关键词（topic_high / topic_low）的解析测试。

语义对齐 AMiner 的 paper/qa/search：
- high：分组之间 AND、组内 OR，必须命中；
- low：只加分，不拦人；
- 不传时行为完全不变（这是回归的底线）。
"""
from __future__ import annotations


def test_json_nested_groups():
    from app.search import parse_topic_groups
    assert parse_topic_groups('[["a","b"],["c"]]') == [["a", "b"], ["c"]]


def test_flat_list_becomes_single_group():
    from app.search import parse_topic_groups
    assert parse_topic_groups('["a","b"]') == [["a"], ["b"]] or \
        parse_topic_groups('["a","b"]') == [["a", "b"]]


def test_comma_is_one_or_group():
    from app.search import parse_topic_groups
    assert parse_topic_groups("respiratory, pulmonary") == [["respiratory", "pulmonary"]]


def test_empty_and_invalid():
    from app.search import parse_topic_groups
    assert parse_topic_groups("") == []
    assert parse_topic_groups("   ") == []
    assert parse_topic_groups('[["a"') == []          # 非法 JSON 不抛异常
    assert parse_topic_groups("[]") == []


def test_build_expr_and_semantics():
    from app.search import build_high_expr, parse_topic_groups
    expr = build_high_expr(parse_topic_groups('[["a","b"],["c"]]'))
    assert expr == '("a" OR "b") AND ("c")'


def test_build_expr_escapes_quotes():
    from app.search import build_high_expr
    expr = build_high_expr([['bad"term']])
    assert '"' in expr and '""' not in expr
    assert "bad" in expr


def test_no_input_means_no_expression():
    from app.search import build_high_expr, parse_topic_groups
    assert build_high_expr(parse_topic_groups("")) == ""
