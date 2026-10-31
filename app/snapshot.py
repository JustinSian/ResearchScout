"""数据集快照：把主库裁剪成一个可随仓库分发的演示库。

为什么需要：别人 clone 下来没数据、没 API key，就只能看代码。
快照让"clone → 起服务 → 问一句"在 2 分钟内跑通，这是可复现性的底线。

裁剪策略（默认 60 位专家 / 1,200 篇论文 / 目标 < 10 MB）：
- 只保留有深档案的专家，按被引取前 N；
- 论文 = 这些专家署名的论文（去重后截断），保留作者关联；
- 边只保留两端都在人选里的；
- 期刊/机构只保留被引用到的行；
- FTS 索引重建（不拷贝影子表，避免 schema 不一致）。

用法：
  python -m app.snapshot                          # 默认写到 data/snapshot/demo.db
  python -m app.snapshot --people 60 --works 1200
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from app.config import DB_PATH, SCHEMA_PATH

# 需要复制数据的表（其余由 schema 建空表即可）
COPY_TABLES = ["people", "works", "work_authors", "edges", "dossier", "dossier_work",
               "venues", "institutions", "orgs", "person_org", "person_impact", "meta"]


def _create_schema(dst: sqlite3.Connection, src: sqlite3.Connection, log=print) -> None:
    """把源库的表结构复制过去（虚拟表直接执行建表语句，不复制影子表）。"""
    rows = src.execute(
        """SELECT type,name,sql FROM sqlite_master
           WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'""").fetchall()
    made = 0
    for typ, name, sql in rows:
        if typ not in ("table", "index", "view"):
            continue
        if typ == "table" and (name.endswith("_data") or name.endswith("_idx")
                               or name.endswith("_content") or name.endswith("_docsize")
                               or name.endswith("_config")):
            continue
        try:
            dst.execute(sql)
            made += 1
        except sqlite3.OperationalError:
            continue      # 索引依赖的表可能被跳过，忽略
    log(f"  建表 {made} 条（含 FTS 虚拟表）")


def build(out: Path | None = None, people: int = 60, works: int = 1200,
          log=print) -> dict:
    src = sqlite3.connect(str(DB_PATH))
    src.row_factory = sqlite3.Row
    out = out or (Path(__file__).resolve().parents[1] / "data" / "snapshot" / "demo.db")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    dst = sqlite3.connect(str(out))

    t0 = time.time()
    _create_schema(dst, src, log=log)

    # 1) 选专家：有深档案 + 按被引降序
    pids = [r[0] for r in src.execute(
        """SELECT p.pid FROM people p JOIN dossier d ON d.pid=p.pid
           ORDER BY p.cited_by_count DESC LIMIT ?""", (people,))]
    if not pids:
        pids = [r[0] for r in src.execute(
            "SELECT pid FROM people WHERE status='published' ORDER BY cited_by_count DESC LIMIT ?",
            (people,))]
    ph = ",".join("?" * len(pids))

    # 2) 选论文：这些人署名的论文，按被引降序截断
    wids = [r[0] for r in src.execute(
        f"""SELECT w.wid FROM works w JOIN work_authors wa ON wa.wid=w.wid
            WHERE wa.pid IN ({ph}) GROUP BY w.wid
            ORDER BY MAX(w.cites) DESC LIMIT ?""", (*pids, works))]
    wh = ",".join("?" * len(wids)) if wids else "NULL"

    def copy(sql_src: str, sql_dst: str, args: tuple = ()) -> int:
        rows = src.execute(sql_src, args).fetchall()
        if not rows:
            return 0
        cols = len(rows[0])
        dst.executemany(sql_dst, [tuple(r) for r in rows])
        return len(rows)

    counts = {}
    counts["people"] = copy(f"SELECT * FROM people WHERE pid IN ({ph})",
                            _insert_sql(src, "people"), tuple(pids))
    counts["works"] = copy(f"SELECT * FROM works WHERE wid IN ({wh})",
                           _insert_sql(src, "works"), tuple(wids))
    counts["work_authors"] = copy(
        f"SELECT * FROM work_authors WHERE wid IN ({wh})",
        _insert_sql(src, "work_authors"), tuple(wids))
    counts["edges"] = copy(
        f"SELECT * FROM edges WHERE a IN ({ph}) AND b IN ({ph})",
        _insert_sql(src, "edges"), (*pids, *pids))
    counts["dossier"] = copy(f"SELECT * FROM dossier WHERE pid IN ({ph})",
                             _insert_sql(src, "dossier"), tuple(pids))
    counts["dossier_work"] = copy(
        "SELECT * FROM dossier_work WHERE pid IN (%s)" % ph,
        _insert_sql(src, "dossier_work"), tuple(pids))
    # 期刊/机构：只保留被引用到的
    counts["venues"] = copy(
        "SELECT * FROM venues WHERE vid IN (SELECT venue_id FROM works WHERE wid IN (%s))" % wh,
        _insert_sql(src, "venues"), tuple(wids))
    counts["institutions"] = copy(
        "SELECT * FROM institutions WHERE iid IN (SELECT inst_id FROM people WHERE pid IN (%s))" % ph,
        _insert_sql(src, "institutions"), tuple(pids))
    counts["person_org"] = copy(
        "SELECT * FROM person_org WHERE pid IN (%s)" % ph,
        _insert_sql(src, "person_org"), tuple(pids))
    counts["orgs"] = copy(
        "SELECT * FROM orgs WHERE oid IN (SELECT oid FROM person_org WHERE pid IN (%s))" % ph,
        _insert_sql(src, "orgs"), tuple(pids))
    # 引用加权：只带 demo 这批人的行，否则会把 3.3 万人的数据塞进 6MB 快照
    counts["person_impact"] = copy(
        "SELECT * FROM person_impact WHERE pid IN (%s)" % ph,
        _insert_sql(src, "person_impact"), tuple(pids))
    counts["meta"] = copy("SELECT * FROM meta", _insert_sql(src, "meta"))

    # 3) 重建 FTS（不拷贝影子表）
    dst.execute("DELETE FROM people_fts")
    dst.executemany(
        "INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
        [(r["pid"], " ".join(x for x in (r["name"], r["name_zh"]) if x),
          " ".join(__import__("json").loads(r["topics"] or "[]")), r["last_inst"] or "")
         for r in src.execute(f"SELECT pid,name,name_zh,topics,last_inst FROM people WHERE pid IN ({ph})",
                              tuple(pids))])
    dst.execute("DELETE FROM works_fts")
    dst.executemany(
        "INSERT INTO works_fts(rowid,title,abstract) VALUES(?,?,?)",
        [(r["wid"], r["title"] or "", r["abstract"] or "")
         for r in src.execute(f"SELECT wid,title,abstract FROM works WHERE wid IN ({wh})", tuple(wids))])

    # 4) 修剪引用完整性：work_authors 里指向"不在快照里的作者"的行会破坏 JOIN 直觉
    #    （论文页显示 37 个作者、点进去 12 个 404）。演示库宁可少人，也要闭合。
    dst.execute("DELETE FROM work_authors WHERE pid NOT IN (SELECT pid FROM people)")
    dst.execute("DELETE FROM dossier_work WHERE pid NOT IN (SELECT pid FROM people)")

    # 修剪后的真实行数（counts 里记的是拷贝前的数量）
    counts["work_authors"] = dst.execute("SELECT COUNT(*) FROM work_authors").fetchone()[0]
    counts["dossier_work"] = dst.execute("SELECT COUNT(*) FROM dossier_work").fetchone()[0]

    dst.commit()
    size_mb = out.stat().st_size / 1024 / 1024
    stats = {"path": str(out), "size_mb": round(size_mb, 2),
             "seconds": round(time.time() - t0, 1), **counts}
    log(f"  快照完成：{out}（{size_mb:.2f} MB，{stats['seconds']}s）")
    log(f"  行数：{counts}")
    src.close()
    dst.close()
    return stats


def _cols(conn: sqlite3.Connection, table: str) -> int:
    return len(conn.execute(f"PRAGMA table_info({table})").fetchall())


def _insert_sql(src: sqlite3.Connection, table: str) -> str:
    n = _cols(src, table)
    return f"INSERT OR REPLACE INTO {table} VALUES ({','.join('?' * n)})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--people", type=int, default=60)
    ap.add_argument("--works", type=int, default=1200)
    a = ap.parse_args()
    build(Path(a.out) if a.out else None, people=a.people, works=a.works)


if __name__ == "__main__":
    main()
