"""短期记忆：会话内的滚动摘要 + 最近若干轮原文。

和长期记忆的分工：
  短期（本模块）    同一个 sid 内的对话历史，超出窗口就压缩成摘要
  长期（user_memory）跨会话的用户事实，带时间有效区间

为什么需要摘要而不是"把历史全塞进 prompt"：
  LLM 的上下文是有限的、按 token 计费的。10 轮之后原文会挤掉工具结果，
  所以窗口外的内容压成一段摘要（谁在问什么、已经查到什么、还差什么）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import DB_PATH

CTX_DB = Path(DB_PATH.parent / "chat_ctx.db")

MAX_TURNS_KEEP = 40          # 每个会话最多保存 40 条消息
WINDOW_TURNS = 8             # 默认读取最近 8 条消息
SUMMARIZE_AFTER = 10         # 达到 10 条消息时，可以生成摘要
SUMMARY_MAX_CHARS = 600      # 保存的摘要最多 600 个字符

DDL = [
    """CREATE TABLE IF NOT EXISTS session_turn (
        id INTEGER PRIMARY KEY, sid TEXT NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, ts REAL NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_turn_sid ON session_turn(sid, id)",
    """CREATE TABLE IF NOT EXISTS session_summary (
        sid TEXT PRIMARY KEY, summary TEXT, turns INTEGER, updated_at REAL)""",
]


def connect() -> sqlite3.Connection:
    CTX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CTX_DB), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    return conn


def append(sid: str, role: str, content: str) -> None:
    """写一轮对话；超过上限自动裁掉最老的。"""
    import time
    sid = (sid or "").strip()
    if not sid or not (content or "").strip():
        return
    conn = connect()
    try:
        conn.execute("INSERT INTO session_turn(sid, role, content, ts) VALUES(?,?,?,?)",
                     (sid, role, content.strip()[:4000], time.time()))
        conn.execute(
            "DELETE FROM session_turn WHERE sid=? AND id NOT IN "
            "(SELECT id FROM session_turn WHERE sid=? ORDER BY id DESC LIMIT ?)",
            (sid, sid, MAX_TURNS_KEEP))
        conn.commit()
    finally:
        conn.close()

# 读取最近的消息，恢复正常顺序
def turns(sid: str, limit: int = WINDOW_TURNS) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT role, content FROM session_turn WHERE sid=? ORDER BY id DESC LIMIT ?",
            (sid, max(1, int(limit)))).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    finally:
        conn.close()

# 统计当前保存的消息数
def count(sid: str) -> int:
    conn = connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM session_turn WHERE sid=?",
                                (sid,)).fetchone()[0])
    finally:
        conn.close()

# 读取现有的摘要
def summary(sid: str) -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT summary FROM session_summary WHERE sid=?",
                           (sid,)).fetchone()
        return (row["summary"] if row else "") or ""
    finally:
        conn.close()

# 新增或更新摘要
def set_summary(sid: str, text: str, n_turns: int) -> None:
    import time
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO session_summary(sid, summary, turns, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(sid) DO UPDATE SET summary=excluded.summary, "
            "turns=excluded.turns, updated_at=excluded.updated_at",
            (sid, (text or "")[:SUMMARY_MAX_CHARS], int(n_turns), time.time()))
        conn.commit()
    finally:
        conn.close()


def reset(sid: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM session_turn WHERE sid=?", (sid,))
        conn.execute("DELETE FROM session_summary WHERE sid=?", (sid,))
        conn.commit()
    finally:
        conn.close()


SUMMARIZE_SYSTEM = (
    "把下面的多轮对话压缩成一段不超过 120 字的摘要，供后续对话使用。"
    "必须保留：用户在找什么、已经确认的条件（国家/机构/方向/时间）、"
    "已经查到的人或论文名、还没解决的问题。只输出摘要本身。"
)


def maybe_summarize(sid: str, log=print) -> str:
    """超过阈值时用 LLM 生成摘要；没有 key 或失败就退回"关键词拼接"摘要。"""
    n = count(sid)
    if n < SUMMARIZE_AFTER:
        return summary(sid)
    hist = turns(sid, limit=MAX_TURNS_KEEP)
    text = "\n".join(f"{t['role']}: {t['content'][:200]}" for t in hist[-24:])
    out = ""
    try:
        from app import llm
        if llm.configured():
            r = llm.chat_text([{"role": "system", "content": SUMMARIZE_SYSTEM},
                               {"role": "user", "content": text}])
            if r.get("ok"):
                out = (r.get("text") or "").strip()
    except Exception:
        out = ""
    if not out:
        # 兜底：取用户问题里最长的几条，保证摘要不会为空
        users = [t["content"] for t in hist if t["role"] == "user"]
        out = "；".join(users[-3:])[:SUMMARY_MAX_CHARS]
    set_summary(sid, out, n)
    return out


def prompt_block(sid: str, summary_text: str = "") -> str:
    """拼成注入 system prompt 的一段（没有历史就返回空串）。"""
    s = summary_text or summary(sid)
    recent = turns(sid, limit=4)
    if not s and not recent:
        return ""
    parts = []
    if s:
        parts.append("【本会话摘要】" + s)
    if recent:
        tail = "\n".join(f"- {t['role']}: {t['content'][:120]}" for t in recent)
        parts.append("【最近几轮】\n" + tail)
    return "\n".join(parts)
