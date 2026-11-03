"""_fts_safe 的回归测试：防止停用词表再混入实词、正则再吞数字。"""
from __future__ import annotations
from app.agent import _fts_safe, _FTS_STOP


def test_content_words_are_not_stopwords():
    for w in ("data", "model", "method", "system", "study", "research",
              "analysis", "application", "theory", "management", "detection"):
        assert w not in _FTS_STOP, f"{w} 是实词，不能当停用词"


def test_digits_preserved():
    for c in ("BMPR2", "FOXP3", "IDH2", "PD-L1", "SARS-CoV-2"):
        assert _fts_safe(c) == c


def test_true_stopwords_removed():
    assert "and" not in _fts_safe("Modeling and Analysis")
    assert "of" not in _fts_safe("Graph of Knowledge")
