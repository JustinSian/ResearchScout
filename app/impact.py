"""引用加权：把"库内引用边"折算成每个人的可审计影响力。

为什么不用 OpenAlex 的 `cited_by_count`：
那是**全站被引**，被综述作者和大团队通讯作者主导，而且跟我们这 11k 篇语料没关系。
库内引用边（`work_refs.ref_wid IS NOT NULL`）是本地证据——某人的论文在**本库**里
被引用了多少次，能逐条追溯，也能用 `/work/{wid}/cited-by` 复核。

口径：
- `in_corpus_cites`：其论文被库内引用的总次数（同一篇被引两次算两次）；
- `cited_works`：被引过的**不同**论文数（防止一篇爆款撑起全部）；
- `impact_per_work`：in_corpus_cites / max(works_count, 1)，压制"靠篇数堆量"。

注意这是**相关性证据**，不是学术影响力绝对值。
"""
from __future__ import annotations

import json
import time

from app.db import connect, init_db

DDL = """
CREATE TABLE IF NOT EXISTS person_impact (
  pid             INTEGER PRIMARY KEY,
  in_corpus_cites INTEGER NOT NULL,
  cited_works     INTEGER NOT NULL,
  impact_per_work REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person_impact ON person_impact(in_corpus_cites DESC);
"""

# 聚合口径抽成常量，测试可以对内存库直接执行同一段 SQL
IMPACT_SQL = """
INSERT INTO person_impact(pid, in_corpus_cites, cited_works, impact_per_work)
SELECT wa.pid,
       COUNT(*)                    AS n,
       COUNT(DISTINCT r.ref_wid)   AS dw,
       ROUND(1.0 * COUNT(*) / MAX(MAX(p.works_count, 1), 1), 2)
FROM work_refs r
JOIN work_authors wa ON wa.wid = r.ref_wid
JOIN people p       ON p.pid  = wa.pid
WHERE r.ref_wid IS NOT NULL
GROUP BY wa.pid
"""


def ensure_schema() -> None:
    init_db()
    conn = connect()
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def build(log=print) -> dict:
    """全量重算（可重复执行）。30k 条库内边，秒级。"""
    ensure_schema()
    conn = connect()
    t0 = time.time()
    try:
        conn.execute("DELETE FROM person_impact")
        conn.execute(IMPACT_SQL)
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) FROM person_impact").fetchone()[0]
        top = conn.execute(
            "SELECT pid, in_corpus_cites FROM person_impact "
            "ORDER BY in_corpus_cites DESC LIMIT 1").fetchone()
        stats = {"people_with_impact": rows,
                 "top_pid": top[0] if top else None,
                 "top_in_corpus_cites": top[1] if top else 0,
                 "seconds": round(time.time() - t0, 2)}
        conn.execute(
            "INSERT INTO meta(k, v) VALUES('person_impact', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(stats, ensure_ascii=False),))
        conn.commit()
        log(f"  引用加权构建：{rows} 人 / 最高 {stats['top_in_corpus_cites']} 次"
            f"（pid={stats['top_pid']}）/ {stats['seconds']}s")
        return stats
    finally:
        conn.close()


def stats() -> dict:
    ensure_schema()
    conn = connect()
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='person_impact'").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM person_impact").fetchone()[0]
        return {"rows": n, "meta": json.loads(row[0]) if row else None}
    finally:
        conn.close()


if __name__ == "__main__":
    build()
