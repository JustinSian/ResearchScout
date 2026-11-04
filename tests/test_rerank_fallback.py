"""交叉编码器可选降级的测试：缺模型时退回词法重叠，不抛异常。"""
from __future__ import annotations

import app.rerank_ce as rc


def test_lexical_scores_overlap():
    q = "graph neural network"
    ps = ["graph neural network for molecular graphs", "quantum gravity phenology"]
    s = rc._lexical_scores(q, ps)
    assert s[0] > s[1]
    assert 0.0 <= s[1] <= 1.0


def test_lexical_empty_query_is_zero():
    assert rc._lexical_scores("", ["a", "b"]) == [0.0, 0.0]


def test_available_false_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "MODEL_DIR", tmp_path / "no-such-model")
    monkeypatch.setattr(rc, "_AVAILABLE", None)
    monkeypatch.setattr(rc, "_STATE", {})
    assert rc.available() is False
    # scores() 仍可用，退回词法
    assert rc.scores("abc", ["abc def", "xyz"]) == [1.0, 0.0]
