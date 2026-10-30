"""尽调档案的读取接口：把 dossier 暴露给 Agent 与前端。

设计：档案本身是"事实层"（全量成果 + 派生指标），
Agent 的 planner 只拿摘要，需要细节时再按 pid 取（对应虚拟文件系统 /dossiers/{pid}）。
"""
from __future__ import annotations

import json

from app.db import connect


def dossier(pid: int, limit_papers: int = 20) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM dossier WHERE pid=?", (pid,)).fetchone()
        if not row:
            return None
        metrics = json.loads(row["metrics_json"] or "{}")
        papers = [dict(r) for r in conn.execute(
            """SELECT title,year,cites,venue,doi FROM dossier_work
               WHERE pid=? ORDER BY is_rep DESC, cites DESC LIMIT ?""",
            (pid, limit_papers))]
        person = conn.execute(
            "SELECT name,name_zh,last_inst,topics FROM people WHERE pid=?", (pid,)).fetchone()
        return {"pid": pid, "name": person["name"] if person else "",
                "name_zh": person["name_zh"] if person else None,
                "inst": person["last_inst"] if person else None,
                "fetched_at": row["fetched_at"],
                "works_total": row["works_total"], "works_loaded": row["works_loaded"],
                "metrics": metrics, "papers": papers}
    finally:
        conn.close()


def dossiers_summary(limit: int = 100) -> dict:
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT d.pid, d.works_loaded, d.metrics_json, p.name, p.name_zh, p.last_inst
               FROM dossier d JOIN people p ON p.pid = d.pid
               ORDER BY d.works_loaded DESC LIMIT ?""", (limit,)).fetchall()
        items = []
        for r in rows:
            m = json.loads(r["metrics_json"] or "{}")
            items.append({"pid": r["pid"], "name": r["name"], "name_zh": r["name_zh"],
                          "inst": r["last_inst"], "works": r["works_loaded"],
                          "h_index": m.get("h_index"), "cites": m.get("cites_sum"),
                          "first_year": m.get("first_year"), "last_year": m.get("last_year")})
        total = sum(i["works"] for i in items)
        return {"count": len(items), "total_works": total,
                "avg_works": round(total / max(len(items), 1), 1), "items": items}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 527
    print(json.dumps(dossier(pid, 5), ensure_ascii=False, indent=1)[:1500])
