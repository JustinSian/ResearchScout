"""国家字段取值的规则测试（纯函数，不联网）。

优先来源必须是"现任机构"，因为库里存的 last_inst 与它同源；
历史任职只能作为降级来源，而且**必须带年份**——踩过的坑：
某些 affiliations 的 years 为空，取第一条等于随机挑一段几十年前的经历，
把曼彻斯特大学的人标成了中国。
"""
from __future__ import annotations


def test_author_source_wins():
    from app.enrich_country import _country_of
    a = {"last_known_institutions": [{"country_code": "us"}],
         "affiliations": [{"institution": {"country_code": "CN"}, "years": [2020]}]}
    assert _country_of(a) == ("US", "author")


def test_affiliation_needs_years():
    from app.enrich_country import _country_of
    a = {"last_known_institutions": [],
         "affiliations": [{"institution": {"country_code": "CN"}, "years": []}]}
    assert _country_of(a) == (None, None)


def test_affiliation_picks_most_recent_year():
    from app.enrich_country import _country_of
    a = {"last_known_institutions": [],
         "affiliations": [
             {"institution": {"country_code": "CN"}, "years": [2005, 2006]},
             {"institution": {"country_code": "GB"}, "years": [2019, 2020]},
         ]}
    assert _country_of(a) == ("GB", "affiliation")


def test_ignores_affiliations_without_country():
    from app.enrich_country import _country_of
    a = {"last_known_institutions": [],
         "affiliations": [{"institution": {"display_name": "X"}, "years": [2021]}]}
    assert _country_of(a) == (None, None)


def test_missing_author_record_is_safe():
    from app.enrich_country import _country_of
    assert _country_of({}) == (None, None)
    assert _country_of({"last_known_institutions": None}) == (None, None)
