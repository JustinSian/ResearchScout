"""verifier 故障注入实验的构造逻辑测试（纯函数）。

只测"注入器"本身：干净 claim 不能被改坏，四类注入必须真的改变关键字段，
跨人注入必须用**对方**的主题词（而不是无意义词）。
"""
from __future__ import annotations


def test_clean_claim_not_mutated():
    from app.ab_verifier import _corrupt
    c = {"kind": "metric", "pid": 1, "field": "cites", "value": 100}
    _corrupt(c)
    assert c["value"] == 100          # 原对象不被改动


def test_metric_injection_changes_value():
    from app.ab_verifier import _corrupt
    out = _corrupt({"kind": "metric", "pid": 1, "field": "cites", "value": 100})
    assert out["value"] == 101


def test_term_injection_is_nonsense():
    from app.ab_verifier import _corrupt
    out = _corrupt({"kind": "term", "pid": 1, "term": "asthma"})
    assert out["term"] != "asthma" and "zzz" in out["term"]


def test_coauthor_injection_adds_three():
    from app.ab_verifier import _corrupt
    out = _corrupt({"kind": "coauthor", "a": 1, "b": 2, "shared": 5})
    assert out["shared"] == 8


def test_domain_nonsense_vs_cross():
    from app.ab_verifier import _corrupt
    base = {"kind": "domain", "pid": 1, "topic": ["asthma", "copd"], "min_hits": 1}
    non = _corrupt(base, "nonsense")
    cross = _corrupt(base, "cross", ["liver", "microbiota"])
    assert non["topic"] == ["medieval", "poetry"]
    assert cross["topic"] == ["liver", "microbiota"]


def test_cross_without_terms_falls_back_to_nonsense():
    from app.ab_verifier import _corrupt
    out = _corrupt({"kind": "domain", "pid": 1, "topic": ["a"], "min_hits": 1},
                   "cross", None)
    assert out["topic"] == ["medieval", "poetry"]
