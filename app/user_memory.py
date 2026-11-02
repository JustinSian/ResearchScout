"""用户长期记忆：三层结构 + 时间有效区间。

和 `app/memory.py` 的区别（很重要）：
  memory.py   —— 任务缓存 + 档案 diff（系统内部状态）
  user_memory —— **用户**的语义/情景/程序记忆（跨会话、带时间区间）

三层：
  mem_fact    语义记忆：用户是谁、做什么、什么偏好（带 valid_from/valid_to）
  mem_event   情景记忆：每次问答的决策、结果、反馈
  mem_policy  程序记忆：学到的策略（RSI 产物，另一张表在 m53 用）

为什么单独一个库：主库 `scholar.db` 有 FTS/向量的大写入，`chat_ctx.db` 已经因为
锁问题迁出去过一次。用户记忆是高频小写入，再分一个 `user_mem.db`，互不阻塞。

时间一律存 UTC ISO8601（`2026-10-31T08:12:00+00:00`），展示层再转本地时区。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH

MEM_DB = Path(os.environ.get("ST_MEM_DB", DB_PATH.parent / "user_mem.db"))

DDL = [
    """CREATE TABLE IF NOT EXISTS mem_fact (
        id INTEGER PRIMARY KEY,
        uid TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_to TEXT,
        source TEXT,
        confidence REAL DEFAULT 1.0,
        policy_gen INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_fact_live ON mem_fact(uid, key) WHERE valid_to IS NULL",
    """CREATE TABLE IF NOT EXISTS mem_event (
        id INTEGER PRIMARY KEY,
        uid TEXT NOT NULL,
        sid TEXT,
        query TEXT,
        action TEXT,
        outcome TEXT,
        feedback TEXT,
        meta_json TEXT,
        ts TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_event_uid ON mem_event(uid, ts DESC)",
]


_ENSURED: set[str] = set()


def connect() -> sqlite3.Connection:
    """连接并在首次使用时建表。

    为什么在 connect 里建：调用方（抽取/召回）为了"记忆故障不影响主流程"
    会吞异常，如果表没建，就会**静默丢记忆**——评测里表现为"全都没记住"，
    排查成本很高（实测踩过）。
    """
    MEM_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEM_DB), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    key = str(MEM_DB)
    if key not in _ENSURED:
        for ddl in DDL:
            conn.execute(ddl)
        conn.commit()
        _ENSURED.add(key)
    return conn


def init() -> None:
    conn = connect()
    try:
        for ddl in DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def now_iso(at: datetime | str | None = None) -> str:
    if isinstance(at, str):
        return at
    dt = at or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _norm_key(key: str) -> str:
    return (key or "").strip().lower()


def add_fact(uid: str, key: str, value: str, *, source: str = "",
             confidence: float = 1.0, at: str | None = None,
             policy_gen: int = 0) -> dict:
    """写入一条事实；同一 (uid,key) 的旧活跃事实自动 expire（保留历史）。

    返回 {id, key, value, valid_from, expired_previous}。
    """
    uid = (uid or "").strip()
    key = _norm_key(key)
    value = (value or "").strip()
    if not uid or not key or not value:
        raise ValueError("uid/key/value 不能为空")
    ts = now_iso(at)
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE mem_fact SET valid_to=? WHERE uid=? AND key=? AND valid_to IS NULL",
            (ts, uid, key))
        expired = cur.rowcount
        cur = conn.execute(
            """INSERT INTO mem_fact(uid, key, value, valid_from, valid_to, source,
                                    confidence, policy_gen, created_at)
               VALUES(?,?,?,?,NULL,?,?,?,?)""",
            (uid, key, value, ts, source or "manual", float(confidence), int(policy_gen), ts))
        conn.commit()
        return {"id": int(cur.lastrowid), "key": key, "value": value,
                "valid_from": ts, "expired_previous": expired}
    finally:
        conn.close()


def expire(uid: str, key: str, at: str | None = None) -> int:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE mem_fact SET valid_to=? WHERE uid=? AND key=? AND valid_to IS NULL",
            (now_iso(at), uid, _norm_key(key)))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def facts(uid: str, *, as_of: str | None = None, only_active: bool = True,
          limit: int = 50) -> list[dict]:
    """读事实。

    only_active=True 只看当前有效；否则连同历史。
    as_of 给定时按"那个时刻有效"过滤——`valid_to == as_of` 视为已过期。
    """
    conn = connect()
    try:
        if as_of:
            sql = ("SELECT * FROM mem_fact WHERE uid=? AND valid_from<=? "
                   "AND (valid_to IS NULL OR valid_to>?) ORDER BY key, valid_from")
            rows = conn.execute(sql, (uid, as_of, as_of)).fetchall()
        elif only_active:
            rows = conn.execute(
                "SELECT * FROM mem_fact WHERE uid=? AND valid_to IS NULL "
                "ORDER BY key, valid_from", (uid,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mem_fact WHERE uid=? ORDER BY key, valid_from DESC",
                (uid,)).fetchall()
        return [dict(r) for r in rows][: max(1, int(limit))]
    finally:
        conn.close()


def timeline(uid: str, limit: int = 200) -> list[dict]:
    """按时间排的事件流：事实变更 + 交互记录，前端记忆视图用。"""
    conn = connect()
    try:
        rows = conn.execute(
            """SELECT 'fact' AS kind, id, key, value, valid_from AS ts, source, confidence
               FROM mem_fact WHERE uid=?
               UNION ALL
               SELECT 'event' AS kind, id, action AS key, query AS value, ts, NULL, NULL
               FROM mem_event WHERE uid=?
               ORDER BY ts DESC LIMIT ?""",
            (uid, uid, max(1, int(limit)))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_event(uid: str, sid: str, query: str, action: str, outcome: str,
              feedback: str | None = None, meta: dict | None = None) -> int:
    uid = (uid or "").strip()
    if not uid:
        raise ValueError("uid 不能为空")
    import json
    conn = connect()
    try:
        cur = conn.execute(
            """INSERT INTO mem_event(uid, sid, query, action, outcome, feedback, meta_json, ts)
               VALUES(?,?,?,?,?,?,?,?)""",
            (uid, sid or "", query or "", action or "", outcome or "",
             feedback, json.dumps(meta or {}, ensure_ascii=False), now_iso()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def events(uid: str, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM mem_event WHERE uid=? ORDER BY ts DESC LIMIT ?",
            (uid, max(1, int(limit)))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stats() -> dict:
    conn = connect()
    try:
        users = conn.execute("SELECT COUNT(DISTINCT uid) FROM mem_fact").fetchone()[0]
        live = conn.execute(
            "SELECT COUNT(*) FROM mem_fact WHERE valid_to IS NULL").fetchone()[0]
        hist = conn.execute("SELECT COUNT(*) FROM mem_fact").fetchone()[0]
        ev = conn.execute("SELECT COUNT(*) FROM mem_event").fetchone()[0]
        return {"users": int(users), "facts_live": int(live), "facts_total": int(hist),
                "events": int(ev), "db": str(MEM_DB)}
    finally:
        conn.close()


def clear(uid: str) -> int:
    """删掉某个用户的所有记忆（演示/测试用）。"""
    conn = connect()
    try:
        n = conn.execute("DELETE FROM mem_fact WHERE uid=?", (uid,)).rowcount
        n += conn.execute("DELETE FROM mem_event WHERE uid=?", (uid,)).rowcount
        conn.commit()
        return n
    finally:
        conn.close()
