"""同义改写回归集：同一个意图，用户可能有多种说法，解析器必须都认。

为什么单独一个文件：这类 bug（"被引 500 以上"解析不到）不会让任何
*功能* 测试失败——它们只是换了个说法。只有把"同一意图 × 多种说法"
显式列成表，才能防住"只认一种写法"的退化。

覆盖维度：被引下限 / 条数 / 国家 / 机构类型 / 机构名 / 排序。
另有一条通用约束：识别出的条件短语**不能残留在 topic 里**（否则会拿它去 FTS 搜）。
"""
from __future__ import annotations

import pytest

from app.facets import parse

CITES_CASES = [
    ("被引超过 500 的专家", 500),      # 前置比较词
    ("被引 500 以上", 500),           # 后置"以上"
    ("被引 500+", 500),               # 加号
    ("被引至少 500", 500),            # 前置"至少"
    ("被引不低于 500", 500),          # 前置"不低于"
    ("被引不少于 500", 500),          # 前置"不少于"
    ("被引 500 起", 500),             # 后置"起"
    ("被引超 500", 500),              # "超"（不是"超过"）
    ("引用超过 500", 500),            # 主语用"引用"
    ("引用 500 以上", 500),
    ("被引五百以上", 500),            # 中文数字
    ("被引一千以上", 1000),
    ("被引>500", 500),
    ("被引>=500", 500),
    ("被引≥500", 500),
    ("大牛", 1000),                   # 只有定性词 → 默认高被引门槛
    ("顶尖专家", 1000),
]


@pytest.mark.parametrize("q,expected", CITES_CASES)
def test_cites_min_phrasings(q, expected):
    assert parse(q)["cites_min"] == expected


LIMIT_CASES = [
    ("前 5 位专家", 5),
    ("前五名专家", 5),               # 中文数字
    ("top 5 专家", 5),
    ("前 10 个", 10),
    ("前十", 10),
]


@pytest.mark.parametrize("q,expected", LIMIT_CASES)
def test_limit_phrasings(q, expected):
    assert parse(q)["limit"] == expected


COUNTRY_CASES = [
    ("中国的专家", "CN"),
    ("国内的专家", "CN"),
    ("中国大陆的专家", "CN"),
    ("美国的教授", "US"),
    ("英国的学者", "GB"),
]


@pytest.mark.parametrize("q,expected", COUNTRY_CASES)
def test_country_phrasings(q, expected):
    assert parse(q)["country"] == expected


KIND_CASES = [
    ("大学的教授", "university"),
    ("高校的老师", "university"),
    ("医院的大夫", "hospital"),
    ("附院的医生", "hospital"),
    ("研究所的专家", "institute"),
    ("实验室的研究员", "institute"),
    ("企业的专家", "company"),
    ("大厂的专家", "company"),
]


@pytest.mark.parametrize("q,expected", KIND_CASES)
def test_inst_kind_phrasings(q, expected):
    assert parse(q)["inst_kind"] == expected


INST_CASES = [
    ("清华的专家", "Tsinghua"),
    ("哈佛的教授", "Harvard"),
    ("协和的医生", "Peking Union Medical"),
    ("复旦的学者", "Fudan"),
]


@pytest.mark.parametrize("q,expected", INST_CASES)
def test_inst_name_phrasings(q, expected):
    assert parse(q)["inst_name"] == expected


SORT_CASES = [
    ("引用最多的专家", "cites"),
    ("被引最高的专家", "cites"),
    ("被引最多的专家", "cites"),
    ("影响力最大的专家", "cites"),
    ("权威专家", "cites"),
    ("论文最多的专家", "works"),
    ("产出最多的专家", "works"),
    ("最近活跃的专家", "recent"),
    ("近年活跃的学者", "recent"),
]


@pytest.mark.parametrize("q,expected", SORT_CASES)
def test_sort_phrasings(q, expected):
    assert parse(q)["sort"] == expected


# 条件短语识别后必须从主题里剥掉，否则会被拿去 FTS 检索（搜垃圾）
CLEAN_CASES = [
    "被引 500 以上",
    "被引五百以上",
    "被引超 500",
    "找几个大牛，被引 500 以上就行",
    "前五名专家",
    "中国大陆的专家",
    "医院的大夫",
    "被引最高的专家",
    "影响力最大的专家",
    "引用 500 以上",
]


@pytest.mark.parametrize("q", CLEAN_CASES)
def test_conditions_do_not_pollute_topic(q):
    assert parse(q)["topic"] == "", f"{q!r} 的条件短语残留进了 topic"


# 反过来：真实主题不能被误伤
TOPIC_KEEP = [
    ("找研究帕金森的专家，被引 500+", "帕金森"),
    ("找呼吸专家，被引五百以上", "呼吸"),
    ("推荐中国的呼吸专家", "呼吸"),
]


@pytest.mark.parametrize("q,expected", TOPIC_KEEP)
def test_real_topic_survives(q, expected):
    assert parse(q)["topic"] == expected


# 没有范围词的裸数字不臆测（"被引 500" 也可能是在说某篇论文）
def test_bare_number_is_not_a_threshold():
    assert parse("被引 500")["cites_min"] is None
