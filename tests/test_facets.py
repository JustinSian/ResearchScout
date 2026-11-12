"""Facet 解析测试：自然语言 → 结构化条件。

只测解析（纯函数），不碰数据库；检索逻辑由集成测试覆盖。
重点覆盖三类容易错的地方：
1. 条件短语必须从主题里剥干净（否则主题变成"呼吸科 前 5"）；
2. 中文主题要能翻成英文检索词（语料是英文）；
3. 解析不到就保持 None，不能硬猜。
"""
from __future__ import annotations


def _p(q):
    from app.facets import parse
    return parse(q)


def test_country_and_kind_and_limit():
    f = _p("中国的呼吸科专家，被引超过 1000，前 5")
    assert f["country"] == "CN"
    assert f["cites_min"] == 1000
    assert f["limit"] == 5
    # 条件短语不能残留在主题里
    assert "1000" not in f["topic"]
    assert "前" not in f["topic"]
    assert f["topic"] == "呼吸"
    assert "respiratory" in f["topic_en"] or "pulmonary" in f["topic_en"]


def test_university_kind_and_city():
    f = _p("美国做机器学习的大学教授")
    assert f["country"] == "US"
    assert f["inst_kind"] == "university"
    assert f["topic"] == "机器学习"
    assert "machine learning" in f["topic_en"]


def test_company_kind():
    f = _p("企业里的 AI 专家")
    assert f["inst_kind"] == "company"
    assert "artificial intelligence" in f["topic_en"]


def test_institution_name_mapping():
    f = _p("清华的高被引学者")
    assert f["inst_name"] == "Tsinghua"
    assert f["cites_min"] == 1000
    assert f["topic"] == ""


def test_sort_intent():
    assert _p("引用最多的呼吸专家")["sort"] == "cites"
    assert _p("论文最多的免疫专家")["sort"] == "works"
    assert _p("最近活跃的机器学习专家")["sort"] == "recent"


def test_no_facets_keeps_defaults():
    """纯主题问题不应该被误加条件。"""
    f = _p("dementia 专家")
    assert f["country"] is None
    assert f["inst_kind"] is None
    assert f["inst_name"] is None
    assert f["cites_min"] is None
    assert f["sort"] == "cites"
    assert "dementia" in f["topic"]


def test_topic_en_translates_known_terms():
    from app.facets import topic_en
    assert "lung cancer" in topic_en("肺癌")
    assert "machine learning" in topic_en("机器学习")
    # 不认识的中文返回空（不硬翻）
    assert topic_en("某种不存在的研究词") == []


def test_facet_notes_are_human_readable():
    f = _p("中国的高被引医院专家")
    notes = f["notes"]
    assert any("国家" in n for n in notes)
    assert any("机构类型" in n for n in notes)


# ---- 同一个意图的多种说法都要认（换说法就失效 = 线上踩坑） ----
import pytest


@pytest.mark.parametrize("q,expected", [
    ("找几个大牛", 1000),                       # 只有"大牛" → 默认高被引
    ("找几个大牛，被引超过 500", 500),
    ("找几个大牛，被引 500 以上就行", 500),      # 后置"以上"
    ("找几个大牛，被引 500+", 500),              # 加号
    ("被引至少 500 的专家", 500),                # 前置"至少"
    ("引用不低于 500 的学者", 500),              # "引用" + "不低于"
    ("被引>=500 的人", 500),
    ("被引 500", None),                          # 没有范围词 → 不臆测
])
def test_cites_min_accepts_multiple_phrasings(q, expected):
    assert _p(q)["cites_min"] == expected


@pytest.mark.parametrize("q", [
    "找几个大牛，被引 500 以上就行",
    "找几位大牛，被引超过 800 也可以",
    "被引至少 500 的学者",
])
def test_cites_phrase_does_not_pollute_topic(q):
    # 数字和口语尾巴不能被当成"主题"，否则会拿它去 FTS 搜
    assert _p(q)["topic"] == ""


def test_topic_survives_when_real_topic_present():
    assert _p("找研究帕金森的专家，被引 500+")["topic"] == "帕金森"
