"""可信判定与融合函数的单测。

这些用例直接来自 M1 试跑踩到的坑，不是编的例子：
- Hinton 无 ORCID 被误判（身份权重过高）
- 会议名/机构名被当成"人"
- RRF/DBSF 是纯函数，可独立验证
"""
from __future__ import annotations

from app.judge import judge
from app.search import distribution_score_fusion, reciprocal_rank_fusion

BASE = {"cite_p50": 5504, "cite_p90": 18368}


def test_famous_without_orcid_is_accepted():
    r = judge({**BASE, "name": "Geoffrey E. Hinton", "orcid": None,
               "sample_works": 13, "sample_cites": 243424,
               "has_inst": "University of Toronto", "topics": ["ML"]})
    assert r["verdict"] == "accept"
    assert r["score"] >= 0.80


def test_single_work_unknown_author_is_not_expert():
    r = judge({**BASE, "name": "James F. Hinton", "orcid": None,
               "sample_works": 1, "sample_cites": 6853})
    assert r["verdict"] == "reject"


def test_conference_name_is_rejected():
    r = judge({**BASE, "name": "RECOMB01: The Fifth Annual International Conference",
               "sample_works": 1, "sample_cites": 26})
    assert r["verdict"] == "reject"
    assert any(x["rule"] == "non_person_word" for x in r["reasons"])


def test_institution_like_name_does_not_auto_accept():
    r = judge({**BASE, "name": "Plant Sciences", "sample_works": 9,
               "sample_cites": 1000})
    assert r["verdict"] != "accept"


def test_orcid_alone_is_not_enough():
    """只有 ORCID、没有任何产出记录的人不该直接发布。"""
    r = judge({**BASE, "name": "Wei Zhang", "orcid": "0000-0001-2345-6789",
               "sample_works": 1, "sample_cites": 0})
    assert r["verdict"] != "accept"


def test_rrf_prefers_documents_in_both_lists():
    a = [(1, 0.9), (2, 0.8), (3, 0.7)]
    b = [(2, 12.0), (4, 9.0)]
    fused = reciprocal_rank_fusion([a, b], k=60)
    assert fused[2] > fused[1]          # 两路都命中
    assert fused[2] > fused[4]


def test_dbsf_keeps_score_magnitude():
    a = [(1, 100.0), (2, 1.0)]
    b = [(3, 1.0)]
    fused = distribution_score_fusion([a, b])
    assert fused[1] > fused[2]


def test_fusion_handles_empty_lists():
    assert reciprocal_rank_fusion([[], []]) == {}
    assert distribution_score_fusion([[], []]) == {}


def test_collective_authorship_is_not_an_expert():
    """专家墙曾把 "Authors Task Force Members" 排到第 1 名（9 篇/5 万被引），必须挡掉。"""
    r = judge({**BASE, "name": "Authors Task Force Members", "sample_works": 9,
               "sample_cites": 51346, "has_inst": "University of Milano",
               "topics": ["Medicine"]})
    assert r["verdict"] == "reject"
    assert any(x["rule"] == "non_person_word" for x in r["reasons"])


def test_real_people_survive_the_collective_filter():
    for name in ("Eric S. Lander", "Peter Libby", "Geoffrey E. Hinton"):
        r = judge({**BASE, "name": name, "sample_works": 9, "sample_cites": 51346,
                   "has_inst": "Broad Institute", "topics": ["Medicine"]})
        assert r["verdict"] == "accept", name