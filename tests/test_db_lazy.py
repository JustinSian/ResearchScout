"""connect() 惰性建表/迁移：任何 DB 首次打开自动补 schema。"""
from __future__ import annotations


def test_connect_auto_creates_schema(monkeypatch, tmp_path):
    import app.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
    db._MIGRATED.clear()
    conn = db.connect()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "people" in tables
    assert "works" in tables


def test_connect_migrates_old_snapshot(monkeypatch, tmp_path):
    """旧表缺列 → connect 后自动补列（模拟仓库里提交的旧快照）。"""
    import sqlite3
    import app.db as db
    p = tmp_path / "old.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE people (pid INTEGER PRIMARY KEY, name TEXT)")
    c.commit(); c.close()
    monkeypatch.setattr(db, "DB_PATH", p)
    db._MIGRATED.clear()
    conn = db.connect()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(people)")}
    conn.close()
    assert "first_author_works" in cols
    assert "source" in cols
