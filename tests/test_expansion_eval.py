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


def test_rrf_merge_orders_by_rank_agreement():
    from app.agent import _rrf_merge
    a = [{"pid": 1}, {"pid": 2}, {"pid": 3}]
    b = [{"pid": 2}, {"pid": 3}, {"pid": 4}]
    out = _rrf_merge([a, b], limit=4)
    pids = [it["pid"] for it in out]
    assert pids[0] == 2                      # 两边都靠前
    assert set(pids[:3]) == {1, 2, 3}
    assert pids[-1] == 4


def test_fts_safe_strips_only_true_stopwords():
    from app.agent import _fts_safe
    # 只剥功能词；实词（analysis/data/model）和数字（3D/BMPR2）必须保留
    assert _fts_safe("3D Shape Modeling and Analysis") == "3D Shape Modeling Analysis"
    assert _fts_safe("Gaucher disease") == "Gaucher disease"


def test_fts_safe_keeps_digits_and_content_words():
    from app.agent import _fts_safe
    assert _fts_safe("BMPR2") == "BMPR2"
    assert _fts_safe("FOXP3") == "FOXP3"
    assert _fts_safe("PD-L1") == "PD-L1"
    assert _fts_safe("Data collection") == "Data collection"
    assert _fts_safe("Multilevel model") == "Multilevel model"
