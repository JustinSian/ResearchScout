"""问答缓存：同一问题（同用户、同语料、同记忆状态）在 TTL 内直接返回。

为什么只做**精确匹配**、不做语义近似：
语义缓存需要把问题向量化并调相似度阈值，阈值一松就会把"看似相关但答错"的
结果返回给用户。学术检索产品宁可慢一点，也不能给错答案。

失效策略（三道）：
1. 语料变化 —— 复用 app.memory.cache_get 里的 version 比对（works/dossier 数量）；
2. 记忆变化 —— key 里带 uid 的活跃事实指纹（条数 + 最近 valid_from）；
3. TTL —— 默认 24 小时，ST_CACHE_TTL 可调。

不缓存：拒答（refused）、超时/取消、任何 not ok 的结果。
拒答会随自动补库变化，"没找到"缓存一天是最糟的体验。
"""
from __future__ import annotations

import hashlib
import os
import re
import time

from app import memory

DEFAULT_TTL = 86400.0
_PUNCT = re.compile(r"[\s，。！？、,.!?；;：:]+")


def _ttl() -> float:
    try:
        return float(os.environ.get("ST_CACHE_TTL", DEFAULT_TTL))
    except (TypeError, ValueError):
        return DEFAULT_TTL


def _norm(q: str) -> str:
    """归一化：去标点/空白 + 小写，避免"找 专家"和"找专家"算两条。"""
    return _PUNCT.sub("", (q or "").lower())


def _mem_fp(uid: str) -> str:
    """记忆指纹：活跃事实条数 + 最近一次更新时间。"""
    if not uid:
        return "-"
    try:
        from app import user_memory
        conn = user_memory.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(valid_from), '') FROM mem_fact "
                "WHERE uid=? AND valid_to IS NULL", (uid,)).fetchone()
            return f"{row[0]}:{row[1]}"
        finally:
            conn.close()
    except Exception:
        return "-"


def key(q: str, uid: str = "") -> str:
    raw = f"{_norm(q)}|{uid}|{_mem_fp(uid)}"
    return "qa:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def get(q: str, uid: str = "") -> dict | None:
    """命中返回结果副本（带 cached=True、cost_usd=0）；未命中/过期返回 None。"""
    v = memory.cache_get(key(q, uid))
    if not v:
        return None
    if time.time() - float(v.get("updated_at") or 0) > _ttl():
        return None
    payload = v.get("payload")
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    out = dict(payload)
    out["cached"] = True
    out["cost_usd"] = 0.0
    return out


def put(q: str, uid: str = "", payload: dict | None = None) -> bool:
    """写入缓存。拒答/中止/失败一律不缓存。"""
    if not payload or not payload.get("ok"):
        return False
    if payload.get("refused") or payload.get("cancelled"):
        return False
    if payload.get("finish_reason") in ("timeout", "cancelled", "llm_error"):
        return False
    memory.cache_put(key(q, uid), {"payload": payload, "ts": time.time()})
    return True


def stats() -> dict:
    """缓存条数（qa: 前缀）与 TTL，供 /status 或运维查看。"""
    from app.db import connect
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM kv WHERE key LIKE 'qa:%'").fetchone()[0]
    finally:
        conn.close()
    return {"entries": int(n), "ttl_s": _ttl()}
