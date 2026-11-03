"""金标清洗工具的纯函数测试。"""
from __future__ import annotations


def test_keywords_drop_stopwords_and_keep_terms():
    from app.clean_expert_gold import keywords
    k = keywords("Association (psychology)")
    assert k == ["association", "psychology"]
    assert "and" not in keywords("Data and Analysis")


def test_blocklist_entries_are_concept_scoped():
    from app.clean_expert_gold import BLOCKLIST
    assert BLOCKLIST["Physical therapy"] == [1423]
    assert BLOCKLIST["Association (psychology)"] == [1612]
