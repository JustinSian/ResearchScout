"""期刊（venue）实体化：从 works.venue 文本建实体表。

为什么需要（对标 AMiner 的 `venue/*` 接口）：
- 现在期刊只是 `works.venue` 的一段文本，2315 个不同字符串，无法聚合、无法判断"常发期刊"；
- 审稿人匹配里，"这位专家常发 JTD / Chest" 是有用信号，但纯文本做不了；
- 还有脏数据：`PubMed` / `Research Square` 是仓储不是期刊，必须区分。

做法：
1. 归一化（小写、去标点、压缩空格）得到 `norm_key`，同 key 视为同一期刊；
2. 仓储/预印本按名字识别为 `kind='repository'`，不参与"期刊"统计；
3. 回填 `works.venue_id`，之后所有统计走 join。
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS venues (
        vid INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        norm_key TEXT UNIQUE,
        kind TEXT DEFAULT 'journal',
        works_count INTEGER DEFAULT 0,
        first_year INTEGER,
        last_year INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS idx_venue_key ON venues(norm_key)",
]

# 仓储 / 预印本 / 索引库：不是期刊，单列一类
_REPOSITORY = re.compile(
    r"^(pubmed|pubmed central|pmc|research square|ssrn|arxiv|biorxiv|medrxiv|"
    r"chemrxiv|preprints?\.?org|scienceopen|figshare|zenodo|dryad|osf)$", re.I)


def norm_key(name: str) -> str:
    """期刊名归一化：小写、去标点、压缩空白。"""
    s = (name or "").strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fa5 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_repository(name: str) -> bool:
    return bool(_REPOSITORY.match((name or "").strip()))


def build(log=print) -> dict:
    """从 works.venue 建 venues 表并回填 works.venue_id（幂等）。"""
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    try:
        works_cols = {r[1] for r in conn.execute("PRAGMA table_info(works)")}
        if "venue_id" not in works_cols:
            conn.execute("ALTER TABLE works ADD COLUMN venue_id INTEGER")
        conn.commit()

        rows = conn.execute(
            """SELECT venue, COUNT(*) n, MIN(year) fy, MAX(year) ly
               FROM works WHERE venue IS NOT NULL AND TRIM(venue) != ''
               GROUP BY venue""").fetchall()
        merged: dict[str, dict] = {}
        for r in rows:
            key = norm_key(r["venue"])
            if not key:
                continue
            cur = merged.get(key)
            if not cur:
                merged[key] = {"name": r["venue"].strip(), "n": r["n"],
                               "fy": r["fy"], "ly": r["ly"]}
            else:  # 同一期刊的多种写法：保留作品数最多的那个写法
                cur["n"] += r["n"]
                cur["fy"] = min(x for x in (cur["fy"], r["fy"]) if x is not None) \
                    if (cur["fy"] is not None or r["fy"] is not None) else None
                cur["ly"] = max(x for x in (cur["ly"], r["ly"]) if x is not None) \
                    if (cur["ly"] is not None or r["ly"] is not None) else None

        for key, v in merged.items():
            conn.execute(
                """INSERT INTO venues(name,norm_key,kind,works_count,first_year,last_year)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(norm_key) DO UPDATE SET
                     name=excluded.name, kind=excluded.kind, works_count=excluded.works_count,
                     first_year=excluded.first_year, last_year=excluded.last_year""",
                (v["name"], key, "repository" if is_repository(v["name"]) else "journal",
                 v["n"], v["fy"], v["ly"]))
        conn.commit()

        # 回填 works.venue_id
        conn.execute(
            """UPDATE works SET venue_id = (
                   SELECT v.vid FROM venues v WHERE v.norm_key = (
                       SELECT lower(trim(w2.venue)) FROM works w2 WHERE w2.wid = works.wid))
               WHERE venue_id IS NULL AND venue IS NOT NULL""")
        # 上面的相关子查询太绕，改用 Python 侧映射（数据量小，稳）
        vmap = {r["norm_key"]: r["vid"] for r in conn.execute("SELECT vid,norm_key FROM venues")}
        works = conn.execute("SELECT wid, venue FROM works WHERE venue IS NOT NULL").fetchall()
        updates = []
        for w in works:
            vid = vmap.get(norm_key(w["venue"]))
            if vid:
                updates.append((vid, w["wid"]))
        conn.executemany("UPDATE works SET venue_id=? WHERE wid=?", updates)
        conn.commit()

        stats = {
            "venues": conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0],
            "journals": conn.execute("SELECT COUNT(*) FROM venues WHERE kind='journal'").fetchone()[0],
            "repositories": conn.execute("SELECT COUNT(*) FROM venues WHERE kind='repository'").fetchone()[0],
            "works_linked": conn.execute(
                "SELECT COUNT(*) FROM works WHERE venue_id IS NOT NULL").fetchone()[0],
            "works_total": conn.execute("SELECT COUNT(*) FROM works").fetchone()[0],
        }
        log(f"  期刊实体 {stats['venues']} 个（期刊 {stats['journals']} / 仓储 {stats['repositories']}），"
            f"论文挂载 {stats['works_linked']}/{stats['works_total']}")
        return stats
    finally:
        conn.close()


def top_venues(limit: int = 20, kind: str = "journal") -> list[dict]:
    conn = connect()
    try:
        sql = """SELECT vid,name,kind,works_count,first_year,last_year FROM venues
                 WHERE (? = '' OR kind = ?) ORDER BY works_count DESC LIMIT ?"""
        return [dict(r) for r in conn.execute(sql, (kind, kind, limit))]
    finally:
        conn.close()


def venue_detail(vid: int, limit: int = 10) -> dict | None:
    conn = connect()
    try:
        v = conn.execute("SELECT * FROM venues WHERE vid=?", (vid,)).fetchone()
        if not v:
            return None
        papers = [dict(r) for r in conn.execute(
            """SELECT wid,title,year,cites FROM works
               WHERE venue_id=? ORDER BY cites DESC LIMIT ?""", (vid, limit))]
        authors = [dict(r) for r in conn.execute(
            """SELECT p.pid,p.name,p.name_zh,COUNT(*) n FROM works w
               JOIN work_authors wa ON wa.wid=w.wid
               JOIN people p ON p.pid=wa.pid
               WHERE w.venue_id=? GROUP BY p.pid ORDER BY n DESC LIMIT ?""", (vid, limit))]
        return {**dict(v), "papers": papers, "prolific_authors": authors}
    finally:
        conn.close()


def person_venues(pid: int, limit: int = 5) -> list[dict]:
    """某位专家常发的期刊（审稿人匹配的关键信号）。"""
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT v.vid,v.name,COUNT(*) n,MAX(w.year) last_year
               FROM works w JOIN work_authors wa ON wa.wid=w.wid
               JOIN venues v ON v.vid=w.venue_id
               WHERE wa.pid=? AND v.kind='journal'
               GROUP BY v.vid ORDER BY n DESC LIMIT ?""", (pid, limit))]
    finally:
        conn.close()


if __name__ == "__main__":
    print(build())
    print(top_venues(5))
