"""多轮收窄（refinement）测试：条件回写自然语言、再解析、只提真实可选项。"""
from __future__ import annotations


def test_to_query_roundtrip():
    """to_query 生成的句子必须能被 parse 重新解析回同样的条件。"""
    from app.facets import parse, to_query
    f = parse("中国的呼吸科专家")
    q = to_query(f)
    assert q == "中国呼吸专家"
    g = parse(q)
    assert g["country"] == "CN"
    assert g["topic"] == "呼吸"


def test_to_query_institution_roundtrip():
    from app.facets import parse, to_query
    f = parse("中国的呼吸科专家")
    q = to_query(f, inst_name="Fudan University")
    assert "机构是Fudan University" in q
    g = parse(q)
    assert g["inst_name"] == "Fudan University"
    # 机构名不能漏进主题，否则 FTS 会去搜 "Fudan" 这个词
    assert g["topic"] == "呼吸"


def test_to_query_limit_roundtrip():
    from app.facets import parse, to_query
    f = parse("中国的呼吸科专家")
    q = to_query(f, limit=20)
    assert "前 20 条" in q
    assert parse(q)["limit"] == 20


def test_suggest_only_real_options():
    """建议只能来自结果里真实存在的机构/国家/被引，不能凭空造。"""
    from app.facets import parse, suggest
    f = parse("中国的呼吸科专家")
    items = [
        {"pid": 1, "inst": "Fudan University", "country": "CN", "cited_by_count": 9000},
        {"pid": 2, "inst": "Fudan University", "country": "CN", "cited_by_count": 6000},
        {"pid": 3, "inst": "Zhejiang University", "country": "CN", "cited_by_count": 3000},
    ]
    refs = suggest(f, items, total=50)
    labels = [r["label"] for r in refs]
    assert any("Fudan University" in x for x in labels)
    # 只有 1 人的机构不该被推荐
    assert not any("Zhejiang University" in x for x in labels)
    # 总共有 50 人但只给了 3 条 → 应该建议扩大
    assert any("扩大" in x for x in labels)
    for r in refs:
        assert r["query"] and r["label"]


def test_suggest_empty_when_nothing_to_add():
    from app.facets import parse, suggest
    f = parse("中国的呼吸科专家，前 10 条")
    items = [{"pid": i, "inst": f"Inst{i}", "country": "CN", "cited_by_count": 100}
             for i in range(10)]
    # 每人机构都不同、没有高被引、条数已够 → 没有可推荐项
    assert suggest(f, items, total=10) == []
