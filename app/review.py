"""复核队列：待审候选的列表与决策（带留痕）。

设计取舍：不做"全自动批量通过"，只做**单条/批量决策 + 理由**，
因为可信采集的价值就在"有人对结果负责"；批量一键通过等于把门槛作废。
"""
from __future__ import annotations

import json
import time

from app.db import connect


def list_queue(limit: int = 20, min_priority: float = 0.0) -> list[dict]:
    """待审候选。

    排序刻意不是"按分数"：分析发现 pending 里 works>=2 的只有约 2.5k（其余是单篇作者），
    而单篇作者分数并不低。所以先按"是否够格（works>=2）"排，再按分数与作品数，
    让复核者的时间花在真正能进库的候选上。
    """
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT p.pid, p.name, p.last_inst, p.works_count, p.cited_by_count,
                      p.confidence, p.topics
               FROM people p
               WHERE p.status='pending' AND p.confidence >= ?
               ORDER BY (p.works_count >= 2) DESC, p.confidence DESC, p.works_count DESC
               LIMIT ?""",
            (min_priority, limit)).fetchall()
        return [{"pid": r["pid"], "name": r["name"], "inst": r["last_inst"],
                 "works": r["works_count"], "cites": r["cited_by_count"],
                 "score": round(float(r["confidence"] or 0), 3),
                 "topics": json.loads(r["topics"] or "[]")[:4]} for r in rows]
    finally:
        conn.close()


def decide(pid: int, decision: str, by: str = "reviewer", note: str = "") -> dict:
    """接受或拒绝一个待审候选；写 review_queue 留痕。"""
    if decision not in ("accept", "reject"):
        raise ValueError("decision 只能是 accept / reject")
    conn = connect()
    try:
        row = conn.execute("SELECT pid,name,status FROM people WHERE pid=?", (pid,)).fetchone()
        if not row:
            raise KeyError(f"pid {pid} 不存在")
        new_status = "published" if decision == "accept" else "rejected"
        conn.execute("UPDATE people SET status=? WHERE pid=?", (new_status, pid))
        row2 = conn.execute("SELECT cid FROM candidates WHERE payload LIKE ? "
                            "ORDER BY cid DESC LIMIT 1", (f'%"pid": {pid}%',)).fetchone()
        conn.execute(
            """INSERT OR REPLACE INTO review_queue(cid,priority,decided_by,decision,note,decided_at)
               VALUES(?,?,?,?,?,?)""",
            (int(row2["cid"]) if row2 else pid, float(row["status"] == "pending"),
             by, decision, note, time.time()))
        conn.commit()
        return {"pid": pid, "name": row["name"], "status": new_status,
                "decided_by": by, "note": note}
    finally:
        conn.close()


def queue_stats() -> dict:
    conn = connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM people WHERE status='pending'").fetchone()[0]
        decided = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE decision IS NOT NULL").fetchone()[0]
        by_decision = {r["decision"]: r["n"] for r in conn.execute(
            """SELECT decision, COUNT(*) n FROM review_queue
               WHERE decision IS NOT NULL GROUP BY decision""")}
        return {"pending": total, "decided": decided, "by_decision": by_decision}
    finally:
        conn.close()
