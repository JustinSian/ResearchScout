"""引用加权口径测试：对内存库执行与生产同一段聚合 SQL，验证语义而不是数字。

口径（见 app/impact.py）：
- in_corpus_cites  其论文被库内引用的总次数（同一篇被引两次算两次）
- cited_works      被引过的不同论文数
- impact_per_work  in_corpus_cites / max(works_count, 1)
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE people (pid INTEGER PRIMARY KEY, works_count INTEGER);
CREATE TABLE works (wid INTEGER PRIMARY KEY);
CREATE TABLE work_authors (wid INTEGER, pid INTEGER);
CREATE TABLE work_refs (wid INTEGER, ref_openalex TEXT, ref_wid INTEGER);
CREATE TABLE person_impact (
  pid INTEGER PRIMARY KEY, in_corpus_cites INTEGER,
  cited_works INTEGER, impact_per_work REAL);
"""


def _db():
    from app.impact import IMPACT_SQL
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    # 人 1：2 篇论文，被库内引用 3 次（w1 两次 / w2 一次）
    conn.execute("INSERT INTO people VALUES (1, 2)")
    # 人 2：1 篇论文，被引 1 次；works_count 为 0 时应按 1 处理（不除零）
    conn.execute("INSERT INTO people VALUES (2, 0)")
    conn.executemany("INSERT INTO works VALUES (?)", [(11,), (12,), (21,)])
    conn.executemany("INSERT INTO work_authors VALUES (?,?)",
                     [(11, 1), (12, 1), (21, 2)])
    conn.executemany("INSERT INTO work_refs VALUES (?,?,?)", [
        (100, "W1", 11), (101, "W2", 11), (102, "W3", 12),   # 引用人 1 的论文
        (103, "W4", 21),                                      # 引用人 2 的论文
        (104, "W5", None),                                    # 库外引用：不计
    ])
    conn.commit()
    conn.execute(IMPACT_SQL)
    conn.commit()
    return conn


def test_counts_and_per_work():
    conn = _db()
    rows = {r[0]: r for r in conn.execute(
        "SELECT pid, in_corpus_cites, cited_works, impact_per_work FROM person_impact")}
    assert rows[1][1] == 3          # 3 次被引
    assert rows[1][2] == 2          # 2 篇不同论文
    assert rows[1][3] == 1.5        # 3 / 2
    conn.close()


def test_zero_works_count_does_not_divide_by_zero():
    conn = _db()
    row = conn.execute(
        "SELECT in_corpus_cites, impact_per_work FROM person_impact WHERE pid=2").fetchone()
    assert row[0] == 1
    assert row[1] == 1.0           # 1 / max(0,1)
    conn.close()


def test_out_of_corpus_refs_are_ignored():
    conn = _db()
    total = conn.execute("SELECT SUM(in_corpus_cites) FROM person_impact").fetchone()[0]
    assert total == 4              # 只算 ref_wid 非空的 4 条
    conn.close()


def test_sort_field_is_registered():
    from app.experts import BASE, SORTS
    assert "impact" in SORTS and "impact DESC" in SORTS["impact"]
    assert "person_impact" in BASE


def test_demo_snapshot_has_impact_table():
    """clone 下来要能直接 sort=impact：快照里必须带这张表。"""
    import sqlite3 as s
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "data" / "snapshot" / "demo.db"
    if not p.exists():
        return
    c = s.connect(str(p))
    try:
        n = c.execute("SELECT COUNT(*) FROM person_impact").fetchone()[0]
    finally:
        c.close()
    assert n > 0
