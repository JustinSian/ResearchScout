"""补库模块两处修复的回归测试：
  1) OpenAlex id 归一（短 ID），合作者查询才能匹配到主库的人；
  2) enrich_person 的指标写**绝对值**，不在本地总数上再加增量。
"""
from __future__ import annotations

import sqlite3

import pytest

from app import expand

SCHEMA = [
    """CREATE TABLE people(pid INTEGER PRIMARY KEY, openalex_id TEXT, name TEXT,
        name_zh TEXT, orcid TEXT, works_count INTEGER, cited_by_count INTEGER,
        h_index INTEGER, last_inst TEXT, country TEXT, topics TEXT, status TEXT,
        confidence REAL, evidence TEXT, updated_at TEXT, source TEXT, source_ref TEXT,
        headline TEXT, endorse TEXT, inst_id INTEGER, country_src TEXT,
        first_author_works INTEGER)""",
    """CREATE TABLE works(wid INTEGER PRIMARY KEY, openalex_id TEXT, doi TEXT,
        title TEXT, abstract TEXT, year INTEGER, cites INTEGER, venue TEXT, type TEXT)""",
    "CREATE TABLE work_authors(wid INTEGER, pid INTEGER, position INTEGER, PRIMARY KEY(wid,pid))",
]


class _FakeOA:
    def works_of_author(self, oid, per_page=50):
        return [{"id": "https://openalex.org/W1", "title": "p1", "cited_by_count": 10,
                 "publication_year": 2020, "authorships": []},
                {"id": "https://openalex.org/W2", "title": "p2", "cited_by_count": 5,
                 "publication_year": 2021, "authorships": []}]

    def _get(self, path, params):
        return {"works_count": 120, "cited_by_count": 6000}


class _BoomProfileOA(_FakeOA):
    def _get(self, path, params):
        raise RuntimeError("profile 挂了")


def test_short_oid_normalizes():
    assert expand._short_oid("https://openalex.org/A123") == "A123"
    assert expand._short_oid("A123") == "A123"
    assert expand._short_oid("https://openalex.org/A123/") == "A123"
    assert expand._short_oid("") == ""
    assert expand._short_oid(None) == ""


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """用文件库（每次 connect 新开一条连接，模拟真实行为）。"""
    path = tmp_path / "t.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    for ddl in SCHEMA:
        c.execute(ddl)
    c.execute("INSERT INTO people(pid,openalex_id,name,works_count,cited_by_count,topics)"
              " VALUES(1,'A123','张三',100,5000,'[]')")
    c.commit()

    def _connect():
        n = sqlite3.connect(str(path))
        n.row_factory = sqlite3.Row
        return n

    monkeypatch.setattr(expand, "connect", _connect)
    monkeypatch.setattr(expand, "_OA", _FakeOA())
    return {"conn": c, "path": str(path), "monkeypatch": monkeypatch}


def _query(db, sql, args=()):
    c = sqlite3.connect(db["path"])
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


def test_enrich_person_writes_absolute_metrics(db):
    r = expand.enrich_person(1, log=lambda *a, **k: None)
    assert r["ok"] is True
    row = _query(db, "SELECT works_count, cited_by_count FROM people WHERE pid=1")
    # 绝对值 120 / 6000，而不是 100+2=102 / 5000+15=5015
    assert row["works_count"] == 120
    assert row["cited_by_count"] == 6000


def test_enrich_person_never_lowers_metrics(db):
    c = sqlite3.connect(db["path"])
    c.execute("UPDATE people SET works_count=999, cited_by_count=99999 WHERE pid=1")
    c.commit()
    c.close()
    expand.enrich_person(1, log=lambda *a, **k: None)
    row = _query(db, "SELECT works_count, cited_by_count FROM people WHERE pid=1")
    assert row["works_count"] == 999 and row["cited_by_count"] == 99999


def test_enrich_person_adds_works_even_if_profile_fails(db):
    db["monkeypatch"].setattr(expand, "_OA", _BoomProfileOA())
    r = expand.enrich_person(1, log=lambda *a, **k: None)
    assert r["ok"] is True and r["added_works"] == 2
    n = _query(db, "SELECT COUNT(*) AS n FROM work_authors WHERE pid=1")
    assert n["n"] == 2
