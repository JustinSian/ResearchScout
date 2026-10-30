"""把外部专家库的"合作网络"接进来（人—组织层）+ 用论文补人—人共著边。

为什么需要两个层：
- 某学术图谱给钟南山的合作对象是**机构/平台**（广州实验室、国家呼吸医学中心…），不是人；
- 我们的 edges 只连人，所以他的合作者数一直是 0。
只加组织层会看不到同行，只加共著边会丢掉平台关系，所以两层都要。

表结构：
  orgs(oid, ext_id, name, name_zh, kind)          —— 机构/平台节点
  person_org(pid, oid, relation, weight, ...)     —— 人—机构关系（隶属/平台/学会…）
"""
from __future__ import annotations

import json
import time

from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS orgs (
        oid INTEGER PRIMARY KEY,
        ext_id TEXT UNIQUE,
        name TEXT NOT NULL,
        name_zh TEXT,
        kind TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS person_org (
        pid INTEGER NOT NULL,
        oid INTEGER NOT NULL,
        relation TEXT,
        weight REAL DEFAULT 1,
        first_year TEXT,
        latest_year TEXT,
        PRIMARY KEY (pid, oid)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_person_org ON person_org(pid)",
]


def _org_kind(ext_id: str) -> str:
    """从外部 id 前缀推断类型：platform-* / inst-* / org-* / partner-*"""
    for pref, kind in (("platform-", "平台"), ("inst-", "机构"),
                       ("org-", "组织"), ("partner-", "合作方")):
        if (ext_id or "").startswith(pref):
            return kind
    return "机构"


def import_collaboration(pid: int, tracks: list[dict], log=print) -> dict:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    n_org = n_rel = 0
    for t in tracks:
        name = (t.get("collaborator_name") or "").strip()
        if not name:
            continue
        ext_id = t.get("collaborator_id") or f"huafu-{name}"
        row = conn.execute("SELECT oid FROM orgs WHERE ext_id=?", (ext_id,)).fetchone()
        if row:
            oid = int(row["oid"])
        else:
            cur = conn.execute(
                "INSERT INTO orgs(ext_id,name,name_zh,kind) VALUES(?,?,?,?)",
                (ext_id, name, name, _org_kind(ext_id)))
            oid = int(cur.lastrowid)
            n_org += 1
        conn.execute(
            """INSERT OR REPLACE INTO person_org(pid,oid,relation,weight,first_year,latest_year)
               VALUES(?,?,?,?,?,?)""",
            (pid, oid, t.get("relation_label") or "", float(t.get("weight") or 1),
             str(t.get("first_year") or ""), str(t.get("latest_year") or "")))
        n_rel += 1
    conn.commit()
    conn.close()
    log(f"  组织 {n_org} 个（新建），关系 {n_rel} 条")
    return {"orgs_created": n_org, "relations": n_rel}


def rebuild_author_edges(wids: list[int], log=print) -> dict:
    """给指定论文补"人—人共著边"（导入的论文原来没有边）。

    只连前 12 位作者，与主库采集时的口径一致（避免 100 作者的大协作论文产生 4950 条边）。
    """
    init_db()
    conn = connect()
    created = updated = 0
    for wid in wids:
        pids = [int(r["pid"]) for r in conn.execute(
            "SELECT pid FROM work_authors WHERE wid=?", (wid,))][:12]
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                a, b = sorted((pids[i], pids[j]))
                old = conn.execute(
                    "SELECT weight FROM edges WHERE a=? AND b=? AND kind='coauthor'",
                    (a, b)).fetchone()
                if old:
                    conn.execute(
                        "UPDATE edges SET weight=? WHERE a=? AND b=? AND kind='coauthor'",
                        ((old["weight"] or 0) + 1, a, b))
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',1)", (a, b))
                    created += 1
    conn.commit()
    conn.close()
    log(f"  共著边：新建 {created}，加权 {updated}")
    return {"created": created, "updated": updated}


def person_orgs(pid: int) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT o.oid, o.name, o.name_zh, o.kind, po.relation, po.weight,
                      po.first_year, po.latest_year
               FROM person_org po JOIN orgs o ON o.oid = po.oid
               WHERE po.pid=? ORDER BY po.weight DESC""", (pid,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--pid", type=int, required=True)
    a = ap.parse_args()
    raw = json.load(open(a.path, encoding="utf-8"))
    print(import_collaboration(a.pid, raw.get("collaboration_tracks") or []))
    conn = connect()
    wids = [int(r["wid"]) for r in conn.execute(
        "SELECT wid FROM work_authors WHERE pid=?", (a.pid,))]
    conn.close()
    print(rebuild_author_edges(wids))
