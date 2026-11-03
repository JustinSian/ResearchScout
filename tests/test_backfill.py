"""位次回填的纯函数测试。"""
from __future__ import annotations


def test_short_id_handles_url_and_bare():
    from app.backfill_positions import short_id
    assert short_id("https://openalex.org/A5027340494") == "A5027340494"
    assert short_id("A5027340494") == "A5027340494"
    assert short_id("") == ""
