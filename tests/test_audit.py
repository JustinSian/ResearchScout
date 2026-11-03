"""盲审打分与一致性的纯函数测试。"""
from __future__ import annotations


def test_score_excludes_border():
    from app.audit_score import score
    r = score({"a": "是", "b": "否", "c": "边界", "d": "是"})
    assert r["yes"] == 2 and r["no"] == 1 and r["border"] == 1
    import pytest
    assert r["precision"] == pytest.approx(2 / 3, abs=1e-3)


def test_wilson_interval_bounds():
    from app.audit_score import score
    r = score({str(i): ("是" if i < 9 else "否") for i in range(10)})
    lo, hi = r["ci95"]
    assert 0 <= lo < r["precision"] < hi <= 1


def test_kappa_perfect_agreement():
    from app.audit_score import kappa
    a = {str(i): "是" for i in range(10)}
    assert kappa(a, dict(a)) == 1.0


def test_kappa_disagreement_is_lower():
    from app.audit_score import kappa
    a = {str(i): ("是" if i % 2 else "否") for i in range(10)}
    b = {str(i): ("否" if i % 2 else "是") for i in range(10)}
    k = kappa(a, b)
    assert k is not None and k < 0


def test_kappa_needs_enough_samples():
    from app.audit_score import kappa
    assert kappa({"1": "是"}, {"1": "是"}) is None
