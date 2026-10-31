"""检索层：FTS5/BM25 + bge-small-en 向量 + RRF/DBSF 融合 + 画像/图谱。

刻意把"召回"与"融合"分开：
- `search_people` / `search_works` / `search_dense`：单路召回
- `reciprocal_rank_fusion` / `distribution_score_fusion`：纯函数，方便单测与 A/B
"""
from __future__ import annotations

import json
import re
import sqlite3

import numpy as np

from app.config import CANDIDATES, RRF_K
from app.db import connect
from app.labels import label_inst, label_topic

_TOKEN = re.compile(r"[\w\-]+", re.UNICODE)

# 查询模板词与英文停用词：不承载检索意图，但会让 FTS5 的 AND 匹配归零。
# 第一版评测 BM25 全线 0.000，就是因为它把 "find Scott M. Grundy" 拆成 4 个必须同时命中的词。
_STOP = {
    "find", "who", "what", "which", "is", "are", "the", "a", "an", "of", "in", "at",
    "on", "for", "with", "works", "work", "working", "researchers", "researcher",
    "experts", "expert", "people", "person", "list", "show", "me", "and", "or",
    "to", "from", "by", "about", "does", "do", "their", "his", "her", "that",
    # 中文模板词：不承载检索意图，进 FTS 会把 AND 匹配打断
    "找", "研究", "专家", "学者", "哪些", "谁", "什么", "介绍", "一下", "合作",
    "论文", "文章", "领域", "方向", "相关", "请", "帮我", "查", "看", "和",
}


# 中文没有空格，`[\w\-]+` 会把"找研究"整段抓成一个 token，停用词表就失效了。
# 所以先在字符串层面剥掉这些模板词，再分词。
_ZH_TEMPLATE = re.compile(
    r"(找研究|帮我找|查一下|帮我|有哪些人|有哪些|是什么|是谁|介绍一下|找|研究|专家|学者|"
    r"介绍|一下|哪些|什么|方向|领域|相关|论文|文章|合作|的|和|与|请|查|看)")


def _fts_query(q: str, mode: str = "and") -> str:
    q = _ZH_TEMPLATE.sub(" ", q or "")
    raw = _TOKEN.findall(q)
    tokens = [t for t in raw if t.lower() not in _STOP]
    # 中文 token 必须保留：导入的外部专家有中文名（钟南山）和中文主题词。
    # 之前为了修"中文提问打断 AND"把所有非 ASCII 都滤掉了，结果中文搜索永远为空。
    tokens = [t for t in tokens
              if (len(t) >= 2 and all("\u4e00" <= ch <= "\u9fa5" for ch in t))
              or any(ch.isascii() and ch.isalpha() for ch in t)]
    if not tokens:
        return ""
    # 单个字符（中间名缩写 "M"）信息量低，去掉以免把匹配卡死
    tokens = [t for t in tokens if len(t) > 1] or tokens
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join(f'"{t}"' for t in tokens)


def _fts_with_relaxation(conn, table: str, join_sql: str, q: str, limit: int,
                         extra_sql: str = "", extra_args: tuple = (),
                         id_col: str = "pid") -> list[tuple[int, float]]:
    """先 AND（精确），没结果再 OR（放宽）。

    为什么要做：`graph neural networks` 在 AND 下只有 5 人命中且都不是"已发布"状态，
    接口就返回空；放宽到 OR 至少给出相关候选，并让上层（Agent）自己决定要不要用。
    """
    for mode in ("and", "or"):
        fq = _fts_query(q, mode)
        if not fq:
            return []
        sql = (f"SELECT f.rowid AS {id_col}, bm25({table}) AS s FROM {table} f "
               f"{join_sql} WHERE {table} MATCH ?{extra_sql} ORDER BY s LIMIT ?")
        try:
            rows = conn.execute(sql, (fq, *extra_args, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        if rows:
            return [(int(r[id_col]), -float(r["s"])) for r in rows]
    return []


# ---------------- 词面召回 ----------------
def search_people(q: str, limit: int = 20, status: str | None = "published",
                  conn: sqlite3.Connection | None = None) -> list[tuple[int, float]]:
    own = conn is None
    conn = conn or connect()
    try:
        return _fts_with_relaxation(
            conn, "people_fts", "JOIN people p ON p.pid = f.rowid", q, limit,
            extra_sql=(" AND p.status = ?" if status else ""),
            extra_args=((status,) if status else ()), id_col="pid")
    finally:
        if own:
            conn.close()


def search_works(q: str, limit: int = 20,
                 conn: sqlite3.Connection | None = None) -> list[tuple[int, float]]:
    own = conn is None
    conn = conn or connect()
    try:
        return _fts_with_relaxation(conn, "works_fts", "", q, limit, id_col="wid")
    finally:
        if own:
            conn.close()


# ---------------- 融合（纯函数） ----------------
def reciprocal_rank_fusion(rankings, k: int = RRF_K) -> dict[int, float]:
    """Cormack et al. RRF：score = Σ 1/(k + rank)。只看名次。"""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, (doc_id, _score) in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return fused


def distribution_score_fusion(rankings) -> dict[int, float]:
    """DBSF：每路按均值/标准差归一后相加。保留分差信息。"""
    fused: dict[int, float] = {}
    for ranking in rankings:
        if not ranking:
            continue
        scores = [s for _i, s in ranking]
        mean = sum(scores) / len(scores)
        var = sum((s - mean) ** 2 for s in scores) / max(len(scores), 1)
        std = var ** 0.5 or 1.0
        for doc_id, score in ranking:
            fused[doc_id] = fused.get(doc_id, 0.0) + (score - mean) / std
    return fused


# ---------------- 稠密召回 ----------------
_CACHE: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def _load_matrix(kind: str) -> tuple[np.ndarray, np.ndarray]:
    """向量一次读进内存：4814 人 ≈ 7MB，8000 篇 ≈ 12MB。"""
    if kind in _CACHE:
        return _CACHE[kind]
    conn = connect()
    try:
        rows = conn.execute("SELECT ref_id, vec FROM vectors WHERE kind=?", (kind,)).fetchall()
    finally:
        conn.close()
    if not rows:
        _CACHE[kind] = (np.zeros((0,), dtype=np.int64), np.zeros((0, 384), dtype=np.float32))
        return _CACHE[kind]
    ids = np.array([int(r["ref_id"]) for r in rows], dtype=np.int64)
    mat = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat = mat / np.maximum(norms, 1e-9)
    _CACHE[kind] = (ids, mat)
    return _CACHE[kind]


def search_dense(q: str, kind: str = "people", limit: int = 50,
                 conn=None) -> list[tuple[int, float]]:
    from app.embed import encode_one

    ids, mat = _load_matrix(kind)
    if ids.size == 0:
        return []
    qv = encode_one(q).astype(np.float32)
    sims = mat @ qv
    k = min(limit, sims.shape[0])
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    return [(int(ids[i]), float(sims[i])) for i in top]


def search_hybrid(q: str, kind: str = "people", limit: int = 20,
                  fusion: str = "rrf", rrf_k: int = RRF_K,
                  candidates: int = CANDIDATES,
                  status: str | None = "published") -> list[tuple[int, float]]:
    """两路召回 + 融合。fusion ∈ {rrf, dbsf}。"""
    lexical = (search_people(q, limit=candidates, status=status) if kind == "people"
               else search_works(q, limit=candidates))
    dense = search_dense(q, kind=kind, limit=candidates)
    if not dense:
        return lexical[:limit]
    if not lexical:
        return dense[:limit]
    fused = (distribution_score_fusion([lexical, dense]) if fusion == "dbsf"
             else reciprocal_rank_fusion([lexical, dense], k=rrf_k))
    return sorted(fused.items(), key=lambda kv: -kv[1])[:limit]


# ---------------- 权威性先验重排 ----------------
def rerank_with_authority(hits: list[tuple[int, float]], lam: float = 0.2,
                          kind: str = "people", limit: int = 10,
                          ) -> list[tuple[int, float]]:
    """在检索分上叠加 log(被引) 先验：score' = score_norm + λ·log1p(cites)。

    动机来自评测：institution / topic 两类查询**召回对了但排序错**——
    BM25 只按词面匹配排序，把高被引的权威学者排到低被引作者后面。
    """
    if not hits or lam <= 0:
        return hits[:limit]
    conn = connect()
    try:
        col, table = ("cited_by_count", "people") if kind == "people" else ("cites", "works")
        id_col = "pid" if kind == "people" else "wid"
        ids = [h[0] for h in hits]
        ph = ",".join("?" * len(ids))
        rows = conn.execute(f"SELECT {id_col} AS id, {col} AS c FROM {table} "
                            f"WHERE {id_col} IN ({ph})", ids).fetchall()
        cites = {int(r["id"]): int(r["c"] or 0) for r in rows}
    finally:
        conn.close()
    import math

    base = [s for _i, s in hits]
    lo, hi = min(base), max(base)
    span = (hi - lo) or 1.0
    scored = []
    for ref_id, s in hits:
        norm = (s - lo) / span
        scored.append((ref_id, norm + lam * math.log1p(cites.get(ref_id, 0)) / 10.0))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:limit]


# ---------------- 画像 / 图谱 ----------------
def person_profile(pid: int) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM people WHERE pid=?", (pid,)).fetchone()
        if not row:
            return None
        works = conn.execute(
            """SELECT w.wid,w.title,w.year,w.cites,w.venue FROM works w
               JOIN work_authors wa ON wa.wid=w.wid
               WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 10""", (pid,)).fetchall()
        collab = conn.execute(
            """SELECT p.pid,p.name,p.last_inst,e.weight FROM edges e
               JOIN people p ON p.pid = CASE WHEN e.a=? THEN e.b ELSE e.a END
               WHERE e.a=? OR e.b=? ORDER BY e.weight DESC LIMIT 10""",
            (pid, pid, pid)).fetchall()
        return {
            "pid": row["pid"], "name": row["name"], "orcid": row["orcid"],
            "works_count": row["works_count"], "cited_by_count": row["cited_by_count"],
            "h_index": row["h_index"], "last_inst": row["last_inst"],
            "country": row["country"],
            # 去重：外部导入的中文主题与 OpenAlex 概念可能重复（"慢阻肺"出现两次）
            "topics": list(dict.fromkeys(json.loads(row["topics"] or "[]"))),
            "topics_zh": list(dict.fromkeys(label_topic(t) for t in json.loads(row["topics"] or "[]"))),
            "inst_zh": label_inst(row["last_inst"]),
            "status": row["status"], "confidence": row["confidence"],
            "headline": row["headline"] if "headline" in row.keys() else None,
            "source": (row["source"] if "source" in row.keys() else None) or "openalex",
            "source_ref": row["source_ref"] if "source_ref" in row.keys() else None,
            "name_zh": row["name_zh"] if "name_zh" in row.keys() else None,
            "works": [dict(w) for w in works],
            "collaborators": [dict(c) for c in collab],
        }
    finally:
        conn.close()


def graph_neighbors(pid: int, depth: int = 1) -> dict:
    """合作网络：SQLite 递归 CTE，不引图数据库。"""
    conn = connect()
    try:
        sql = """
        WITH RECURSIVE g(pid, depth) AS (
            SELECT ?, 0
            UNION
            SELECT CASE WHEN e.a = g.pid THEN e.b ELSE e.a END, g.depth + 1
            FROM edges e JOIN g ON e.a = g.pid OR e.b = g.pid
            WHERE g.depth < ?
        )
        SELECT DISTINCT pid, MIN(depth) AS depth FROM g GROUP BY pid
        """
        nodes = [dict(r) for r in conn.execute(sql, (pid, depth))]
        ids = [n["pid"] for n in nodes]
        if not ids:
            return {"nodes": [], "edges": []}
        ph = ",".join("?" * len(ids))
        edges = [dict(r) for r in conn.execute(
            f"SELECT a,b,weight FROM edges WHERE kind='coauthor' AND a IN ({ph}) AND b IN ({ph})",
            ids + ids)]
        people = {r["pid"]: dict(r) for r in conn.execute(
            f"SELECT pid,name,name_zh,last_inst,works_count,country FROM people WHERE pid IN ({ph})", ids)}
        # 社区（Louvain）用于前端着色：同一社区同色，能看出"学派/圈子"
        try:
            comm = {r["pid"]: (r["community"], r["csize"]) for r in conn.execute(
                f"SELECT pid,community,csize FROM person_community WHERE pid IN ({ph})", ids)}
        except Exception:
            comm = {}
        out_nodes = []
        for n in nodes:
            base = people.get(n["pid"], {})
            c = comm.get(n["pid"], (None, 0))
            out_nodes.append({"pid": n["pid"], "depth": n["depth"], **base,
                              "community": c[0], "csize": c[1]})
        return {"nodes": out_nodes, "edges": edges}
    finally:
        conn.close()
