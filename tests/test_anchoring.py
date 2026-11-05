"""引用软锚定：只在答案里逐字存在时挂锚，绝不凭空造。"""
from __future__ import annotations

from app.anchoring import anchor_citations, _needles


def test_person_name_anchors_when_present():
    cites = [{"kind": "person", "id": 1, "label": "钟南山"}]
    out = anchor_citations("钟南山是呼吸病学专家", cites)
    assert out[0]["anchor"] == "钟南山"


def test_person_name_english():
    cites = [{"kind": "person", "id": 1, "label": "Nanshan Zhong"}]
    out = anchor_citations("Nanshan Zhong 就职于广州", cites)
    assert out[0]["anchor"] == "Nanshan Zhong"


def test_no_anchor_when_absent():
    cites = [{"kind": "person", "id": 1, "label": "钟南山"}]
    out = anchor_citations("某位学者做了这项研究", cites)
    assert out[0]["anchor"] is None


def test_long_title_falls_back_to_longest_word():
    cites = [{"kind": "work", "id": 9,
              "label": "Programmable editing of a target base in genomic DNA without double-stranded DNA cleavage"}]
    # 答案里只出现 "Programmable"，完整标题太长不会逐字出现
    out = anchor_citations("他的代表作是 Programmable 那篇", cites)
    assert out[0]["anchor"] == "Programmable"


def test_empty_citations_and_answer():
    assert anchor_citations("", []) == []
    assert anchor_citations("x", []) == []


def test_needles_short_label_not_split():
    assert _needles("钟南山") == ["钟南山"]
    assert _needles("Nanshan Zhong") == ["Nanshan Zhong"]
