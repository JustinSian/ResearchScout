"""中文主题清洗 + 翻译的测试（决定能否补库/检索到）。"""
from __future__ import annotations
from app.expand import clean_topic, to_english


def test_clean_strips_user_phrasing():
    assert clean_topic("我想认识建筑") == "建筑"
    assert clean_topic("推荐几个建筑领域的专家") == "建筑"
    assert clean_topic("CRISPR base editing") == "CRISPR base editing"


def test_to_english_maps_common_fields():
    assert to_english("建筑") == "architecture"
    assert to_english("人工智能") == "artificial intelligence"
    assert to_english("建筑与城市规划") in ("architecture", "urban planning")


def test_to_english_passes_through_english():
    assert to_english("Gastroenterology") == "Gastroenterology"
    assert to_english("CRISPR base editing") == "CRISPR base editing"
