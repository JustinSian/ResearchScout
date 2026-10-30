"""补齐缺失的 FTS 索引行。

背景：`expand_author` 新建人物时只写了 people 表，没写 people_fts；
健康检查 `/health/trust` 报出 47,569 人 vs 41,002 行 FTS —— 6,567 人搜不到。

这个脚本把缺失的行补上（增量，不重建整表），并可以被重复执行。
"""
from __future__ import annotations

import json

from app.db import connect, init_db


def repair(log=print) -> dict:
    init_db()
    conn = connect()
    missing = conn.execute(
        """SELECT p.pid, p.name, p.name_zh, p.topics, p.last_inst
           FROM people p WHERE p.pid NOT IN (SELECT rowid FROM people_fts)""").fetchall()
    log(f"  缺失 {len(missing)} 行")
    # 别名也要进 FTS，否则中文名搜不到
    try:
        aliases = {int(r["pid"]): r["alias"] for r in conn.execute(
            "SELECT pid, alias FROM person_alias")}
    except Exception:
        aliases = {}
    for r in missing:
        name = " ".join(x for x in (r["name"], r["name_zh"], aliases.get(int(r["pid"]))) if x)
        topics = " ".join(json.loads(r["topics"] or "[]"))
        conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                     (r["pid"], name, topics, r["last_inst"] or ""))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    fts = conn.execute("SELECT COUNT(*) FROM people_fts").fetchone()[0]
    conn.close()
    log(f"  补完后：people {total} / fts {fts}")
    return {"missing": len(missing), "people": total, "fts": fts}


if __name__ == "__main__":
    print(repair())
