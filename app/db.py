"""SQLite 访问层。单进程 + WAL，够这个规模（500 人 / 1 万论文）用。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from app.config import DB_PATH, SCHEMA_PATH


_MIGRATED: set[str] = set()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 惰性升级：任何 DB（包括仓库里提交的旧快照）首次打开自动补表/补列。
    # 顺序：先 migrate 补缺失列（旧表缺列时 executescript 里的索引会失败），
    #       再 executescript 建缺失的表/索引。
    key = str(DB_PATH.resolve())
    if key not in _MIGRATED:
        migrate(conn)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        _MIGRATED.add(key)
    return conn


def init_db() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect() as conn:
        migrate(conn)
        conn.executescript(sql)
        conn.commit()


# 增量迁移：只补缺失的列（幂等）
MIGRATIONS = [
    ("people", "source", "TEXT DEFAULT 'openalex'"),
    ("people", "source_ref", "TEXT"),
    ("people", "headline", "TEXT"),
    ("people", "first_author_works", "INTEGER DEFAULT 0"),
    ("people", "name_zh", "TEXT"),
    # 背书来源：NULL = 独立核验；'network' = 仅凭合作网络进入发布集
    ("people", "endorse", "TEXT"),
]


def migrate(conn: sqlite3.Connection) -> None:
    # 只在表存在时补列：fresh DB 还没有表，PRAGMA 返回空、不能 ALTER 不存在的表
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, col, decl in MIGRATIONS:
        if table not in tables:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def set_meta(conn: sqlite3.Connection, k: str, v: Any) -> None:
    conn.execute("INSERT INTO meta(k, v) VALUES(?, ?) "
                 "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                 (k, json.dumps(v, ensure_ascii=False)))


def get_meta(conn: sqlite3.Connection, k: str, default: Any = None) -> Any:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def stats(conn: sqlite3.Connection) -> dict:
    def one(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "people": one("SELECT COUNT(*) FROM people"),
        "published": one("SELECT COUNT(*) FROM people WHERE status='published'"),
        "works": one("SELECT COUNT(*) FROM works"),
        "edges": one("SELECT COUNT(*) FROM edges"),
        "candidates": one("SELECT COUNT(*) FROM candidates"),
        "review_pending": one("SELECT COUNT(*) FROM review_queue WHERE decision IS NULL"),
        "db_mb": round(DB_PATH.stat().st_size / 1e6, 1) if DB_PATH.exists() else 0,
    }


def upsert_person(conn: sqlite3.Connection, p: dict) -> int:
    cur = conn.execute(
        """INSERT INTO people(openalex_id,name,orcid,works_count,cited_by_count,h_index,
                              last_inst,country,topics,status,confidence,evidence,updated_at)
           VALUES(:openalex_id,:name,:orcid,:works_count,:cited_by_count,:h_index,
                  :last_inst,:country,:topics,:status,:confidence,:evidence,:updated_at)
           ON CONFLICT(openalex_id) DO UPDATE SET
             name=excluded.name, orcid=excluded.orcid, works_count=excluded.works_count,
             cited_by_count=excluded.cited_by_count, h_index=excluded.h_index,
             last_inst=excluded.last_inst, country=excluded.country, topics=excluded.topics,
             status=CASE WHEN people.status='published' THEN 'published' ELSE excluded.status END,
             confidence=excluded.confidence, evidence=excluded.evidence,
             updated_at=excluded.updated_at""",
        p,
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute("SELECT pid FROM people WHERE openalex_id=?",
                       (p["openalex_id"],)).fetchone()
    return int(row["pid"])


def upsert_work(conn: sqlite3.Connection, w: dict) -> int:
    cur = conn.execute(
        """INSERT INTO works(openalex_id,doi,title,abstract,year,cites,venue,type)
           VALUES(:openalex_id,:doi,:title,:abstract,:year,:cites,:venue,:type)
           ON CONFLICT(openalex_id) DO UPDATE SET
             doi=excluded.doi, title=excluded.title, abstract=excluded.abstract,
             year=excluded.year, cites=excluded.cites, venue=excluded.venue,
             type=excluded.type""",
        w,
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute("SELECT wid FROM works WHERE openalex_id=?",
                       (w["openalex_id"],)).fetchone()
    return int(row["wid"])


def link_author(conn: sqlite3.Connection, wid: int, pid: int, position: int) -> None:
    conn.execute("INSERT OR REPLACE INTO work_authors(wid,pid,position) VALUES(?,?,?)",
                 (wid, pid, position))


def upsert_edge(conn: sqlite3.Connection, a: int, b: int, kind: str = "coauthor") -> None:
    if a == b:
        return
    lo, hi = (a, b) if a < b else (b, a)
    conn.execute("""INSERT INTO edges(a,b,kind,weight) VALUES(?,?,?,1)
                    ON CONFLICT(a,b,kind) DO UPDATE SET weight=weight+1""",
                 (lo, hi, kind))


def upsert_institution(conn: sqlite3.Connection, inst: dict) -> int | None:
    if not inst or not inst.get("openalex_id"):
        return None
    conn.execute("""INSERT INTO institutions(openalex_id,name,country,type)
                    VALUES(?,?,?,?)
                    ON CONFLICT(openalex_id) DO UPDATE SET
                      name=excluded.name, country=excluded.country, type=excluded.type""",
                 (inst["openalex_id"], inst["name"], inst.get("country"), inst.get("type")))
    row = conn.execute("SELECT iid FROM institutions WHERE openalex_id=?",
                       (inst["openalex_id"],)).fetchone()
    return int(row["iid"]) if row else None


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """全量重建 FTS（contentless 表只能手动维护）。"""
    conn.execute("DELETE FROM people_fts")
    conn.execute("DELETE FROM works_fts")
    rows: Iterable[sqlite3.Row] = conn.execute(
        "SELECT pid,name,topics,last_inst FROM people")
    for r in rows:
        topics = " ".join(json.loads(r["topics"] or "[]"))
        conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                     (r["pid"], r["name"], topics, r["last_inst"] or ""))
    for w in conn.execute("SELECT wid,title,abstract FROM works"):
        conn.execute("INSERT INTO works_fts(rowid,title,abstract) VALUES(?,?,?)",
                     (w["wid"], w["title"] or "", w["abstract"] or ""))
    conn.commit()
