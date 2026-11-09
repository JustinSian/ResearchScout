"""中间件层：限流 / 脱敏 / 审计 / 成本核算。

对标简历里的"可插拔中间件：在模型调用、工具调用、文件读写、任务状态变更等节点注入 Hook"。
这里的实现是**确定性、本地化**的：不依赖 Redis/Kafka，用 SQLite + 进程内令牌桶。

为什么要它：Agent 一旦能自己调工具，就必然需要回答四个问题——
  1. 谁在什么时候调了什么？（审计）
  2. 会不会被一次请求打爆？（限流）
  3. 日志里会不会写进敏感信息？（脱敏）
  4. 一次任务花了多少钱/多少请求？（成本）

Hook 点：
  before_tool(tool, args) → 可能抛 RateLimited
  after_tool(tool, args, status, ms, artifact) → 写审计行
  on_error(tool, args, exc) → 记录失败原因（不吞异常）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass

from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS audit (
        aid INTEGER PRIMARY KEY,
        ts REAL, tid INTEGER, sid INTEGER,
        tool TEXT, args_hash TEXT, args_redacted TEXT,
        status TEXT, ms INTEGER, artifact TEXT, summary TEXT,
        cost_units REAL DEFAULT 0, error TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_tid ON audit(tid)",
]

# 每个工具的成本（单位：外部请求数 / 计算量，用于估算）
TOOL_COST = {
    "researcher": 6.0,     # 多通道检索
    "analyst": 2.0,        # 读档案
    "verifier": 1.5,
    "dossier": 1.0,
    "resolve": 1.0,
    "contrast": 0.5,
    "score": 0.5,
    "report": 0.2,
    "extract_topic": 0.1,
}


@dataclass
class RateLimited(Exception):
    detail: str = "工具调用过于频繁，请稍后再试"

# 令牌桶限流算法
"""容量：60
补充速度：60 个/分钟，也就是 1 个/秒
初始令牌：60"""

class TokenBucket:
    """进程内令牌桶：容量 capacity，每分钟补充 refill 个。"""

    def __init__(self, capacity: int = 60, refill_per_min: int = 60):
        self.capacity = capacity
        self.refill = refill_per_min
        self.tokens = float(capacity)
        self.last = time.monotonic()
        self.calls: deque[float] = deque(maxlen=500)

    def take(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last
        self.tokens = min(self.capacity, self.tokens + elapsed * (self.refill / 60.0))
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            self.calls.append(now)
            return True
        return False


_BUCKET = TokenBucket()
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9}|\+?\d{2,3}[-\s]?\d{7,11})(?!\d)")
_LONG_ID = re.compile(r"\b(A\d{9,}|\d{15,})\b")     # OpenAlex id / 超长数字


def redact(text: str) -> str:
    """脱敏：邮箱、长 ID、手机号。

    顺序有讲究：先匹配长 ID（A5101475024 这种），否则手机号正则
    会把 "5101475024" 当成号码，输出成 A[phone]（踩过）。
    """
    s = _LONG_ID.sub("[id]", str(text))
    s = _EMAIL.sub("[email]", s)
    s = _PHONE.sub("[phone]", s)
    return s


def init() -> None:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    conn.close()


def before_tool(tool: str, args: dict) -> None:
    """工具调用前的钩子：限流。权重按工具成本给（自己写死，避免饥饿）。"""
    weight = 1.0 if TOOL_COST.get(tool, 1.0) < 3 else 2.0
    if not _BUCKET.take(weight):
        raise RateLimited(f"限流：{tool} 被拒（令牌不足，每分钟 {_BUCKET.refill} 次）")


def after_tool(tid: int, sid: int, tool: str, args: dict, status: str,
               ms: int, artifact: str | None = None, summary: str = "",
               error: str | None = None, log=print) -> dict:
    """工具调用后的钩子：写审计行 + 成本核算。"""
    payload = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    row = {
        "ts": time.time(), "tid": tid, "sid": sid, "tool": tool,
        "args_hash": hashlib.sha1(payload.encode()).hexdigest()[:12],
        "args_redacted": redact(payload)[:400],
        "status": status, "ms": ms, "artifact": artifact or "",
        "summary": (summary or "")[:200], "error": error,
        "cost_units": TOOL_COST.get(tool, 1.0),
    }
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO audit(ts,tid,sid,tool,args_hash,args_redacted,status,ms,
                                 artifact,summary,cost_units,error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (row["ts"], row["tid"], row["sid"], row["tool"], row["args_hash"],
             row["args_redacted"], row["status"], row["ms"], row["artifact"],
             row["summary"], row["cost_units"], row["error"]))
        conn.commit()
    finally:
        conn.close()
    if status != "done":
        log(f"      [audit] {tool} {status}: {error or summary}")
    return row


def audit_rows(tid: int | None = None, limit: int = 50) -> list[dict]:
    conn = connect()
    try:
        if tid:
            rows = conn.execute("SELECT * FROM audit WHERE tid=? ORDER BY aid", (tid,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit ORDER BY aid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def summary(tid: int) -> dict:
    """一次任务的成本与耗时汇总。"""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT COUNT(*) n, IFNULL(SUM(cost_units),0) cost, IFNULL(SUM(ms),0) ms,
                      SUM(CASE WHEN status!='done' THEN 1 ELSE 0 END) failed
               FROM audit WHERE tid=?""", (tid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


if __name__ == "__main__":
    init()
    print(redact("联系 a@b.com 或 13800138000，OpenAlex A5101475024"))
    print(summary(12))
