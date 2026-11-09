"""合并 `people` 表里的重复身份（同一位作者被存成两条）。

## 为什么会有重复

早期代码把 OpenAlex 作者 ID **原样**（`https://openalex.org/A...`）写进 `people`，
后来统一成短 ID（`A...`），查重按短 ID 进行。于是同一个作者留下两行：

| | 老行（短 ID） | 新行（全 URL） |
|---|---|---|
| source | `openalex`（初始采集） | `auto_expand`（自动补库） |
| status | 可能 `pending` | `published` |
| topics | 标签丰富 | 常为空 |
| 数值 | 偏低 | 全量 |
| 关系 | 少量 work_authors / 部分 edges | 补库带入的 10 篇等 |

## 合并策略

以**短 ID 行**为身份（target），把全 URL 行（dup）并入：

- 数值取 `max`（works / cited / first_author_works）
- `topics` 取并集（保留老行丰富的标签）
- `status`：任一为 published 则 published
- `source`：任一为 auto_expand 则 auto_expand（继续走影子索引隔离，不影响主索引统计）
- `work_authors` 迁移（`INSERT OR IGNORE`），`edges` 重映射（冲突取 `MAX` 避免虚高）
- 其余以 pid 为键的表：目标没有就搬，有就删（避免破坏主键）
- 重建两个 FTS 索引里的目标行，删掉重复行的索引条目
- 删除重复的 people 行

## 用法

```bash
python -m app.dedupe_people --dry-run    # 只打印计划，不写库
python -m app.dedupe_people --apply      # 执行（先自动备份 DB）
```
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time

from app.config import DB_PATH
from app.db import connect
from app.expand import _index_person, _short_oid


def find_duplicates(conn: sqlite3.Connection) -> list[dict]:
    """找出"全 URL 行 + 短 ID 行"指向同一作者的成对记录。"""
    out: list[dict] = []
    rows = conn.execute(
        "SELECT pid, openalex_id FROM people WHERE openalex_id LIKE 'http%'").fetchall()
    for r in rows:
        short = _short_oid(r["openalex_id"])
        if not short:
            continue
        twin = conn.execute("SELECT pid FROM people WHERE openalex_id=?", (short,)).fetchone()
        if twin:
            out.append({"dup": int(r["pid"]), "target": int(twin["pid"]),
                        "short": short, "full": r["openalex_id"]})
    return out


def _load(conn, pid):
    row = conn.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
    return dict(row) if row else {}


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _topics(raw) -> list[str]:
    try:
        return [str(x) for x in json.loads(raw or "[]") if x]
    except Exception:
        return []


def merge_one(conn: sqlite3.Connection, dup: int, target: int, dry_run: bool = True) -> dict:
    """把 dup 合并进 target。dry_run=True 只统计不动库。"""
    d, t = _load(conn, dup), _load(conn, target)
    if not d or not t:
        return {"skipped": True, "reason": "行不存在"}

    stats = {
        "work_authors": conn.execute(
            "SELECT COUNT(*) FROM work_authors WHERE pid=?", (dup,)).fetchone()[0],
        "edges": conn.execute(
            "SELECT COUNT(*) FROM edges WHERE a=? OR b=?", (dup, dup)).fetchone()[0],
        "name": d.get("name"),
    }
    if dry_run:
        return stats

    # ---- 1) 合并标量字段 ----
    works = max(_int(d.get("works_count")), _int(t.get("works_count")))
    cited = max(_int(d.get("cited_by_count")), _int(t.get("cited_by_count")))
    faw = max(_int(d.get("first_author_works")), _int(t.get("first_author_works")))
    topics: list[str] = []
    for src in (_topics(t.get("topics")), _topics(d.get("topics"))):
        for x in src:
            if x not in topics:
                topics.append(x)
    status = "published" if "published" in (t.get("status"), d.get("status")) \
        else (t.get("status") or d.get("status"))
    source = "auto_expand" if "auto_expand" in ((t.get("source") or ""), (d.get("source") or "")) \
        else (t.get("source") or d.get("source"))
    conn.execute(
        """UPDATE people SET works_count=?, cited_by_count=?, first_author_works=?,
                  topics=?, status=?, source=?, updated_at=datetime('now')
           WHERE pid=?""",
        (works, cited, faw, json.dumps(topics, ensure_ascii=False), status, source, target))

    # ---- 2) 论文—作者关系 ----
    conn.execute("INSERT OR IGNORE INTO work_authors(wid,pid,position) "
                 "SELECT wid,?,position FROM work_authors WHERE pid=?", (target, dup))
    conn.execute("DELETE FROM work_authors WHERE pid=?", (dup,))

    # ---- 3) 合作边：重映射 + 冲突取 MAX（避免合并后权重虚高）----
    for e in conn.execute(
            "SELECT a,b,kind,weight FROM edges WHERE a=? OR b=?", (dup, dup)).fetchall():
        a = target if _int(e["a"]) == dup else _int(e["a"])
        b = target if _int(e["b"]) == dup else _int(e["b"])
        if a == b:            # 自环（原来指的是同一个人）
            continue
        a, b = min(a, b), max(a, b)
        conn.execute(
            """INSERT INTO edges(a,b,kind,weight) VALUES(?,?,?,?)
               ON CONFLICT(a,b,kind) DO UPDATE SET weight=MAX(weight, excluded.weight)""",
            (a, b, e["kind"], _int(e["weight"])))
    conn.execute("DELETE FROM edges WHERE a=? OR b=?", (dup, dup))

    # ---- 4) 以 pid 为键的附属表 ----
    for tbl in ("person_impact", "person_community", "dossier"):
        has = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE pid=?", (target,)).fetchone()[0]
        if has:
            conn.execute(f"DELETE FROM {tbl} WHERE pid=?", (dup,))
        else:
            conn.execute(f"UPDATE {tbl} SET pid=? WHERE pid=?", (target, dup))
    for tbl in ("person_org", "person_alias", "dossier_work", "name_zh_log", "dossier_change"):
        try:
            conn.execute(f"UPDATE {tbl} SET pid=? WHERE pid=?", (target, dup))
        except sqlite3.IntegrityError:
            conn.execute(f"DELETE FROM {tbl} WHERE pid=?", (dup,))

    # ---- 5) 索引：删重复行，重建目标行（保持影子索引隔离）----
    for fts in ("people_fts", "people_fts_exp"):
        try:
            conn.execute(f"DELETE FROM {fts} WHERE rowid=?", (dup,))
        except Exception:
            pass
    inst = conn.execute(
        "SELECT i.name AS n FROM institutions i JOIN people p ON p.inst_id=i.iid WHERE p.pid=?",
        (target,)).fetchone()
    _index_person(conn, target, t.get("name") or d.get("name") or "", topics,
                  inst["n"] if inst else None)

    # ---- 6) 删除重复行 ----
    conn.execute("DELETE FROM people WHERE pid=?", (dup,))
    return stats


def run(apply: bool = False, backup: bool = True, log=print) -> dict:
    conn = connect()
    try:
        dups = find_duplicates(conn)
    finally:
        conn.close()
    if not dups:
        log("  没有发现重复身份")
        return {"ok": True, "duplicates": 0, "merged": 0, "applied": apply}
    if apply and backup:
        bak = DB_PATH.with_name(f"{DB_PATH.stem}.bak.{int(time.time())}{DB_PATH.suffix}")
        shutil.copyfile(DB_PATH, bak)
        log(f"  已备份数据库: {bak}")
    merged = 0
    conn = connect()
    try:
        for d in dups:
            st = merge_one(conn, d["dup"], d["target"], dry_run=not apply)
            log(f"  pid {d['dup']} → {d['target']}  {d['short']}  {st}")
            if apply:
                merged += 1
        if apply:
            conn.commit()
    finally:
        conn.close()
    return {"ok": True, "duplicates": len(dups), "merged": merged, "applied": apply}


def _main() -> None:
    ap = argparse.ArgumentParser(description="合并 people 表里的重复身份")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    ap.add_argument("--no-backup", action="store_true", help="跳过自动备份")
    a = ap.parse_args()
    r = run(apply=a.apply, backup=not a.no_backup)
    print(r)


if __name__ == "__main__":
    _main()
