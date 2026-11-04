"""人名 vs 主题的区分：人名不能当主题去搜 OpenAlex。"""
from __future__ import annotations
from app.expand import clean_topic, to_english


def test_quantity_words_stripped():
    assert clean_topic("我想找一个考古的专家") == "考古"
    assert clean_topic("找几个地质的专家") == "地质"


def test_archaeology_mapped():
    assert to_english("考古") == "archaeology"


def test_person_name_not_treated_as_topic():
    # 人名清洗后应保持原样（不会被误当主题切碎），由 _resolve_person 识别
    assert clean_topic("Édouard Bard") == "Édouard Bard"
