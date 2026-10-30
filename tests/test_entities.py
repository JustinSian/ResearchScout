"""实体层测试：期刊归一、机构归一、仓储识别、近似重复的关系判定。

这些函数是纯逻辑，不碰网络与模型；只有 near_duplicates 会读库，
所以这里只测它的词集合与关系判定规则（通过 _tokens 间接验证）。
"""
from __future__ import annotations

import pytest


# ---------------- 期刊归一 ----------------
def test_venue_norm_key_merges_case_and_punctuation():
    from app.venues import norm_key

    assert norm_key("Science") == norm_key("science")
    assert norm_key("Journal of Biological Chemistry") == \
        norm_key("journal of biological chemistry.")
    assert norm_key("  CHEST  Journal ") == "chest journal"


def test_repository_detection():
    from app.venues import is_repository

    for name in ("PubMed", "PubMed Central", "Research Square", "SSRN", "arXiv", "bioRxiv"):
        assert is_repository(name) is True, name
    for name in ("Science", "Nature", "Journal of Thoracic Disease", "CHEST Journal"):
        assert is_repository(name) is False, name


# ---------------- 机构归一 ----------------
def test_institution_norm_merges_variants():
    from app.institutions import norm_key

    a = norm_key("First Affiliated Hospital of Guangzhou Medical University")
    b = norm_key("The First Affiliated Hospital, Guangzhou Medical University")
    c = norm_key("FIRST AFFILIATED HOSPITAL, GUANGZHOU MEDICAL UNIVERSITY")
    assert a == b == c


def test_institution_norm_expands_abbreviations():
    from app.institutions import norm_key

    assert norm_key("State Key Lab of Respiratory Disease") == \
        norm_key("State Key Laboratory of Respiratory Disease")
    assert norm_key("Dept of Med, Univ of X") == norm_key("Department of Medical, University of X")


def test_institution_does_not_merge_different_hospitals():
    """编号是区分信息，不能被吃掉。"""
    from app.institutions import norm_key

    assert norm_key("Wuxi People's Hospital") != norm_key("Wuxi No.2 People's Hospital")


def test_institution_kind_classification():
    from app.institutions import kind_of

    assert kind_of("First Affiliated Hospital of Guangzhou Medical University") == "hospital"
    assert kind_of("广州医科大学附属第一医院") == "hospital"
    assert kind_of("Harvard University") == "university"
    assert kind_of("State Key Laboratory of Respiratory Disease") == "institute"
    assert kind_of("Kurabo Industries Ltd.") == "company"


def test_tokens_keep_short_discriminative_words():
    """第三次踩坑的回归：短词（no/2/a/f）必须保留，否则不同机构会被判成同一家。"""
    from app.institutions import _tokens

    t1 = _tokens("wuxi no 2 people s hospital")
    t2 = _tokens("wuxi people s hospital")
    assert t1 != t2
    assert {"no", "2"} <= t1
    # 词集合相同但顺序不同 → 视为同一组词（Jaccard=1），由人工确认
    assert _tokens("york university") == _tokens("university york")


def test_abbreviation_table_covers_common_forms():
    from app.institutions import _ABBR

    for k in ("univ", "hosp", "med", "inst", "lab", "natl"):
        assert k in _ABBR
