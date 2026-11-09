"""重复身份合并的测试：用临时库构造"全 URL + 短 ID"两条记录，验证合并结果。"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app import dedupe_people as dp

SCHEMA = [
    """CREATE TABLE people(pid INTEGER PRIMARY KEY, openalex_id TEXT, name TEXT,
        name_zh TEXT, orcid TEXT, works_count INTEGER, cited_by_count INTEGER,
        h_index INTEGER, last_inst TEXT, country TEXT, topics TEXT, status TEXT,
        confidence REAL, evidence TEXT, updated_at TEXT, source TEXT, source_ref TEXT,
        headline TEXT, endorse TEXT, inst_id INTEGER, country_src TEXT,
        first_author_works INTEGER)""",
    """CREATE TABLE work_authors(wid INTEGER, pid INTEGER, position INTEGER,
        PRIMARY KEY(wid,pid))""",
    """CREATE TABLE edges(a INTEGER, b INTEGER, kind TEXT, weight INTEGER,
        PRIMARY KEY(a,b,kind))""",
    "CREATE TABLE person_impact(pid INTEGER PRIMARY KEY, score REAL)",
    "CREATE TABLE person_community(pid INTEGER PRIMARY KEY, cid INTEGER)",
    "CREATE TABLE person_org(pid INTEGER, oid INTEGER, PRIMARY KEY(pid,oid))",
    "CREATE TABLE person_alias(pid INTEGER, alias TEXT, PRIMARY KEY(pid,alias))",
    "CREATE TABLE dossier(pid INTEGER PRIMARY KEY, n INTEGER)",
    "CREATE TABLE dossier_work(pid INTEGER, wid INTEGER, PRIMARY KEY(pid,wid))",
    "CREATE TABLE name_zh_log(pid INTEGER, old TEXT, new TEXT)",
    "CREATE TABLE dossier_change(cid INTEGER PRIMARY KEY, pid INTEGER)",
    "CREATE TABLE institutions(iid INTEGER PRIMARY KEY, name TEXT)",
    "CREATE VIRTUAL TABLE people_fts USING fts5(name, topics, inst, tokenize='unicode61')",
    "CREATE VIRTUAL TABLE people_fts_exp USING fts5(name, topics, inst, tokenize='unicode61')",
]


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for ddl in SCHEMA:
        c.execute(ddl)
    # target：短 ID（老行，标签丰富，pending）
    c.execute("INSERT INTO people(pid,openalex_id,name,works_count,cited_by_count,"
              "topics,status,source,first_author_works) VALUES(?,?,?,?,?,?,?,?,?)",
              (100, "A123", "张三", 1, 13000, json.dumps(["Krabbe disease"]), "pending",
               "openalex", 1))
    # dup：全 URL（补库行，数值全量，标签空，published）
    c.execute("INSERT INTO people(pid,openalex_id,name,works_count,cited_by_count,"
              "topics,status,source,first_author_works) VALUES(?,?,?,?,?,?,?,?,?)",
              (900, "https://openalex.org/A123", "张三", 88, 15915, json.dumps([]),
               "published", "auto_expand", 10))
    c.execute("INSERT INTO work_authors VALUES(1,100,3)")
    c.execute("INSERT INTO work_authors VALUES(2,900,0)")
    c.execute("INSERT INTO edges VALUES(100,55,'coauthor',2)")
    c.execute("INSERT INTO edges VALUES(55,900,'coauthor',3)")   # 与上一行是同一对关系
    c.execute("INSERT INTO person_impact VALUES(100,1.0)")
    c.execute("INSERT INTO person_impact VALUES(900,2.0)")
    c.execute("INSERT INTO dossier VALUES(900,7)")
    c.execute("INSERT INTO person_org VALUES(900,3)")
    c.execute("INSERT INTO institutions VALUES(3,'某大学')")
    c.commit()
    return c


def test_find_duplicates_detects_pair(conn):
    dups = dp.find_duplicates(conn)
    assert dups == [{"dup": 900, "target": 100, "short": "A123",
                     "full": "https://openalex.org/A123"}]


def test_dry_run_does_not_write(conn):
    dp.merge_one(conn, 900, 100, dry_run=True)
    assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 2


def test_merge_fields_and_relations(conn):
    dp.merge_one(conn, 900, 100, dry_run=False)

    # 只剩一条
    assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    row = dict(conn.execute("SELECT * FROM people WHERE pid=100").fetchone())
    # 数值取 max
    assert row["works_count"] == 88 and row["cited_by_count"] == 15915
    assert row["first_author_works"] == 10
    # 标签取并集（老行标签不能丢）
    assert "Krabbe disease" in json.loads(row["topics"])
    # 任一 published 则 published；source 保留 auto_expand（继续影子索引隔离）
    assert row["status"] == "published"
    assert row["source"] == "auto_expand"

    # 关系迁移 + 主键去重
    assert conn.execute("SELECT COUNT(*) FROM work_authors WHERE pid=100").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM work_authors WHERE pid=900").fetchone()[0] == 0
    # 合作边重映射到 100，冲突取 MAX(而非相加)
    w = conn.execute("SELECT weight FROM edges WHERE a=55 AND b=100").fetchone()[0]
    assert w == 3
    assert conn.execute("SELECT COUNT(*) FROM edges WHERE a=900 OR b=900").fetchone()[0] == 0
    # 附属表单行：目标已有则删重复
    assert conn.execute("SELECT COUNT(*) FROM person_impact WHERE pid=100").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM person_impact WHERE pid=900").fetchone()[0] == 0
    # 目标没有的附属表：搬过来
    assert conn.execute("SELECT COUNT(*) FROM dossier WHERE pid=100").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM person_org WHERE pid=100").fetchone()[0] == 1


def test_merge_indexes_target_in_shadow(conn):
    dp.merge_one(conn, 900, 100, dry_run=False)
    assert conn.execute("SELECT COUNT(*) FROM people_fts_exp WHERE rowid=100").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM people_fts WHERE rowid=100").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM people_fts_exp WHERE rowid=900").fetchone()[0] == 0


def test_merge_is_idempotent_after_done(conn):
    dp.merge_one(conn, 900, 100, dry_run=False)
    assert dp.find_duplicates(conn) == []
    # 再跑一次不会崩（行已不存在）
    assert dp.merge_one(conn, 900, 100, dry_run=False).get("skipped") is True
