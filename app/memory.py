"""记忆层：任务缓存 + 档案变更 diff。

对标简历里的"全生命周期记忆：短期上下文 + 长期经验沉淀"。
这里不引 Redis/Milvus，用 SQLite 两张表：

  kv(key, value_json, updated_at)        任务级缓存（同一问题不重跑）
  dossier_change(pid, ts, diff_json)     档案快照差异（专家"变了什么"）

两个关键设计：
1. **缓存带语料版本**：库里论文数/建档数变了，缓存自动失效（否则会用旧结论骗人）。
2. **变更 diff 是增量的**：重建档案时只记"作品数 +N / h-index +X / 新主题 / 新期刊"，
   不重算历史。
"""
from __future__ import annotations

import hashlib
import json
import time

from app.db import connect, init_db

DDL = [
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value_json TEXT, updated_at REAL)",
    """CREATE TABLE IF NOT EXISTS dossier_change (
        cid INTEGER PRIMARY KEY, pid INTEGER NOT NULL, ts REAL,
        diff_json TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_dchg ON dossier_change(pid)",
]


def init() -> None:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()


def corpus_version() -> dict:
    conn = connect()
    try:
        return {"works": conn.execute("SELECT COUNT(*) FROM works").fetchone()[0],
                "dossiers": conn.execute("SELECT COUNT(*) FROM dossier").fetchone()[0]}
    finally:
        conn.close()


def task_key(intent: str, query: str) -> str:
    """任务缓存键：意图 + 归一化问题（去掉标点/空白差异）。"""
    import re
    q = re.sub(r"[\s，。！？、,.!?；;：:]+", "", (query or "").lower())
    h = hashlib.sha1(f"{intent}|{q}".encode()).hexdigest()[:16]
    return f"task:{intent}:{h}"


def cache_get(key: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT value_json, updated_at FROM kv WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    val = json.loads(row["value_json"])
    # 语料变了 → 缓存作废
    if val.get("version") != corpus_version():
        return None
    return {**val, "updated_at": row["updated_at"]}


def cache_put(key: str, value: dict) -> None:
    payload = {**value, "version": corpus_version()}
    conn = connect()
    try:
        conn.execute("INSERT INTO kv(key,value_json,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                     "updated_at=excluded.updated_at",
                     (key, json.dumps(payload, ensure_ascii=False), time.time()))
        conn.commit()
    finally:
        conn.close()


def cache_stats() -> dict:
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
        hits = conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'task:%'").fetchone()[0]
        return {"entries": n, "task_entries": hits}
    finally:
        conn.close()


# ---------------- 档案变更 ----------------
def _snapshot(metrics: dict) -> dict:
    return {"works": metrics.get("works"), "h": metrics.get("h_index"),
            "cites": metrics.get("cites_sum"), "last_year": metrics.get("last_year"),
            "top_venues": [v[0] for v in (metrics.get("top_venues") or [])[:5]]}


def diff_and_store(pid: int, metrics: dict, log=print) -> dict | None:
    """重建档案后调用：与上一次快照比，记录"变了什么"。"""
    init()
    cur = _snapshot(metrics)
    conn = connect()
    try:
        row = conn.execute(
            "SELECT diff_json FROM dossier_change WHERE pid=? ORDER BY cid DESC LIMIT 1",
            (pid,)).fetchone()
    finally:
        conn.close()
    prev = None
    if row:
        try:
            prev = json.loads(row["diff_json"]).get("after")
        except Exception:
            prev = None
    if not prev:
        # 第一次建档：只记基线，不算变更
        conn = connect()
        try:
            conn.execute("INSERT INTO dossier_change(pid,ts,diff_json) VALUES(?,?,?)",
                         (pid, time.time(), json.dumps({"after": cur, "kind": "baseline"},
                                                       ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        return None

    changed = {}
    for k in ("works", "h", "cites", "last_year"):
        if prev.get(k) is not None and cur.get(k) is not None and cur[k] != prev[k]:
            changed[k] = {"from": prev[k], "to": cur[k], "delta": cur[k] - prev[k]}
    new_venues = [v for v in cur.get("top_venues") or [] if v not in (prev.get("top_venues") or [])]
    if new_venues:
        changed["new_venues"] = new_venues
    if not changed:
        return None
    payload = {"after": cur, "changed": changed, "kind": "delta"}
    conn = connect()
    try:
        conn.execute("INSERT INTO dossier_change(pid,ts,diff_json) VALUES(?,?,?)",
                     (pid, time.time(), json.dumps(payload, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    log(f"    [memory] pid={pid} 变更: {list(changed)}")
    return changed


def changes(pid: int, limit: int = 5) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT ts,diff_json FROM dossier_change WHERE pid=? ORDER BY cid DESC LIMIT ?",
            (pid, limit)).fetchall()
        return [{"ts": r["ts"], **json.loads(r["diff_json"])} for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    init()
    print(corpus_version(), cache_stats())
