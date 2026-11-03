"""核心金标评测的纯函数测试（不联网）。"""
from __future__ import annotations


def test_keywords_for_concept():
    from app.eval_core import keywords
    assert keywords("Normalization (sociology)") == ["normalization", "sociology"]
    assert keywords("Data and Analysis") == []
