"""扩充评测的纯函数测试（不联网、不写库）。"""
from __future__ import annotations


def test_keywords_drop_stopwords():
    from app.expand import _keywords
    kws = _keywords("Scientific Computing and Data Management")
    assert "computing" in kws and "scientific" in kws
    assert "and" not in kws and "management" not in kws


def test_work_matches_full_phrase():
    from app.expand import _work_matches
    assert _work_matches("Niemann-Pick disease",
                         {"title": "Niemann-Pick disease type C: a review"})


def test_work_matches_keywords():
    from app.expand import _work_matches
    w = {"title": "Colorectal cancer screening with colonoscopy in Europe"}
    assert _work_matches("Colorectal Cancer Screening and Detection", w)
    assert not _work_matches("Colorectal Cancer Screening and Detection",
                             {"title": "Deep learning for image segmentation"})


def test_work_matches_needs_two_keywords():
    from app.expand import _work_matches
    # 只命中一个关键词不算
    assert not _work_matches("Colorectal Cancer Screening",
                             {"title": "Cancer immunotherapy review"})


def test_gap_topics_shape():
    import json
    from pathlib import Path
    p = Path("eval/gap_topics.jsonl")
    if not p.exists():
        return
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows
    for r in rows:
        assert r["topic"] and r["gold_openalex_ids"]
        assert r["pool_size"] >= 5
