"""钉住 expand.py 文档里承诺的"写入策略"：避免文档又跑到代码前面。

文档（模块 docstring）现在写的是：
  - 通过验证且有数据（works>=1 或 cited>=1）的新作者 → published；
  - 验证不通过或数据为空 → pending；
  - 已有作者只允许 pending → published，且来源必须是 auto_expand。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import expand

SCHEMA = """CREATE TABLE people(
    pid INTEGER PRIMARY KEY, openalex_id TEXT, name TEXT, orcid TEXT,
    works_count INTEGER, cited_by_count INTEGER, last_inst TEXT, country TEXT,
    topics TEXT, status TEXT, confidence REAL, evidence TEXT, source TEXT,
    updated_at REAL)"""


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    return c


def _author(works=5, cited=100, oid="A1"):
    return {"id": "https://openalex.org/" + oid, "display_name": "张三",
            "works_count": works, "cited_by_count": cited,
            "topics": [{"display_name": "Neuroscience"}],
            "last_known_institutions": [{"display_name": "某大学", "country_code": "CN"}]}


def _row(conn, pid):
    return dict(conn.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone())


def test_validated_with_data_is_published(conn):
    pid = expand._upsert_person(conn, _author(), False, topic="krabbe", validated=True)
    r = _row(conn, pid)
    assert r["status"] == "published"
    assert r["source"] == "auto_expand"
    # 补进来的主题要进标签，否则检索不到
    assert "krabbe" in json.loads(r["topics"])
    assert r["country"] == "CN"


def test_validated_without_data_stays_pending(conn):
    pid = expand._upsert_person(conn, _author(works=0, cited=0), False,
                                topic="krabbe", validated=True)
    assert _row(conn, pid)["status"] == "pending"


def test_not_validated_stays_pending(conn):
    pid = expand._upsert_person(conn, _author(), False, topic="krabbe", validated=False)
    assert _row(conn, pid)["status"] == "pending"


def test_existing_auto_expand_pending_can_be_promoted(conn):
    pid = expand._upsert_person(conn, _author(works=0, cited=0), False,
                                topic="krabbe", validated=False)
    assert _row(conn, pid)["status"] == "pending"
    # 第二次带着数据补进来 → 允许 pending → published
    same = expand._upsert_person(conn, _author(), False, topic="krabbe", validated=True)
    assert same == pid
    assert _row(conn, pid)["status"] == "published"


def test_existing_library_person_is_not_promoted_by_expansion(conn):
    conn.execute(
        "INSERT INTO people(openalex_id,name,works_count,cited_by_count,status,source,topics)"
        " VALUES('A2','李四',10,200,'pending','openalex','[]')")
    pid = _row(conn, 1)["pid"]
    same = expand._upsert_person(conn, _author(oid="A2"), False, topic="krabbe", validated=True)
    assert same == pid
    # 主库原有作者的 pending 状态不该被补库流程顺手改掉
    assert _row(conn, pid)["status"] == "pending"
