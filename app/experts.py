"""专家墙：浏览/排序/筛选发布专家（前端"炫酷展示"的数据源）。

设计要点：
- 只查 status='published'（4,633 人），排序里带"合作者数"需要子查询，
  在 4.6k 行上可接受；36k 人全表排序会慢一个数量级。
- facets（主题/机构分布）算一次缓存 300 秒，避免每次请求都解析 4.6k 行 JSON。
"""
from __future__ import annotations

import json
import time

from app.db import connect
from app.labels import label_inst, label_topic

_FACETS: dict = {"at": 0.0, "data": None}
SORTS = {
    "cites": "p.cited_by_count DESC",
    "works": "p.works_count DESC",
    "collab": "collab DESC, p.cited_by_count DESC",
    "recent": "p.pid DESC",
}
BASE = """
SELECT p.pid, p.name, p.last_inst, p.works_count, p.cited_by_count, p.country,
       p.confidence, p.topics, p.headline, p.source, p.name_zh, p.endorse,
       (SELECT COUNT(*) FROM edges e WHERE e.a = p.pid OR e.b = p.pid) AS collab,
       (SELECT COUNT(*) FROM person_org po WHERE po.pid = p.pid) AS org_count
FROM people p
WHERE p.status = 'published'
"""


def _facets(conn) -> dict:
    if _FACETS["data"] and time.time() - _FACETS["at"] < 300:
        return _FACETS["data"]
    topics: dict[str, int] = {}
    insts: dict[str, int] = {}
    for r in conn.execute("SELECT topics, last_inst FROM people WHERE status='published'"):
        for t in json.loads(r["topics"] or "[]"):
            topics[t] = topics.get(t, 0) + 1
        if r["last_inst"]:
            insts[r["last_inst"]] = insts.get(r["last_inst"], 0) + 1
    data = {
        # name 是库里的英文原值（筛选用），name_zh 只用于显示
        "topics": [{"name": k, "name_zh": label_topic(k), "count": v} for k, v in
                   sorted(topics.items(), key=lambda kv: -kv[1])[:28]],
        "insts": [{"name": k, "name_zh": label_inst(k), "count": v} for k, v in
                  sorted(insts.items(), key=lambda kv: -kv[1])[:28]],
    }
    _FACETS.update(at=time.time(), data=data)
    return data


def top_experts(sort: str = "cites", topic: str = "", inst: str = "",
                source: str = "", endorse: str = "", near: int = 0,
                country: str = "", inst_kind: str = "", cites_min: int = 0,
                limit: int = 24, offset: int = 0, order: str = "desc") -> dict:
    order_sql = SORTS.get(sort, SORTS["cites"])
    if order == "asc":
        order_sql = order_sql.replace(" DESC", " ASC")
    where, args = "", []
    if topic:
        where += ' AND p.topics LIKE ?'
        args.append(f'%"{topic}"%')
    if inst:
        where += " AND p.last_inst LIKE ?"
        args.append(f"%{inst}%")
    if source:
        where += " AND IFNULL(p.source,'openalex') = ?"
        args.append(source)
    # Facet 过滤（与 app/facets.py 的维度保持一致，供专家墙筛选条使用）
    if country:
        where += " AND p.country = ?"
        args.append(country)
    if inst_kind:
        where += " AND p.inst_id IN (SELECT iid FROM institutions WHERE kind = ?)"
        args.append(inst_kind)
    if cites_min:
        where += " AND p.cited_by_count >= ?"
        args.append(int(cites_min))
    if endorse == "verified":
        where += " AND p.endorse IS NULL"
    elif endorse == "network":
        where += " AND p.endorse = 'network'"
    if near:
        # 只看某位专家的合作网络（专家墙上"按网络筛选"）
        where += (" AND p.pid IN (SELECT CASE WHEN e.a=? THEN e.b ELSE e.a END "
                  "FROM edges e WHERE (e.a=? OR e.b=?) AND e.kind='coauthor')")
        args += [near, near, near]
    # 按"合作强度"排序：需要把共同论文数取出来，所以单独拼一段 SQL
    shared_sql = ""
    shared_args: tuple = ()
    if near and sort == "network":
        shared_sql = (", (SELECT e.weight FROM edges e WHERE e.kind='coauthor' AND "
                      "((e.a=? AND e.b=p.pid) OR (e.b=? AND e.a=p.pid))) AS shared")
        shared_args = (near, near)
        order_sql = "shared DESC, p.cited_by_count DESC"
    conn = connect()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM people p WHERE p.status='published'" + where,
            args).fetchone()[0]
        sql = (BASE.replace("WHERE p.status = 'published'",
                            "WHERE p.status = 'published'" + where)
               + f" ORDER BY {order_sql} LIMIT ? OFFSET ?")
        if shared_sql:
            # BASE 以 SELECT ... FROM people p 开头：把补充列插到 FROM 之前
            sql = sql.replace("FROM people p", shared_sql + " FROM people p", 1)
        rows = conn.execute(sql, (*shared_args, *args,
                                  min(max(limit, 1), 60), max(offset, 0))).fetchall()
        items = []
        for i, r in enumerate(rows, start=offset + 1):
            items.append({
                "rank": i, "pid": r["pid"], "name": r["name"],
                "inst": r["last_inst"], "inst_zh": label_inst(r["last_inst"]),
                "works": r["works_count"], "org_count": r["org_count"],
                "shared": (r["shared"] if "shared" in r.keys() else None),
                "cites": r["cited_by_count"], "collab": r["collab"],
                "confidence": r["confidence"],
                "headline": r["headline"], "source": r["source"] or "openalex",
                "country": r["country"],
                "endorse": r["endorse"],
                "name_zh": r["name_zh"],
                "topics": json.loads(r["topics"] or "[]")[:5],
                "topics_zh": [label_topic(x) for x in json.loads(r["topics"] or "[]")[:5]],
            })
        return {"total": total, "offset": offset, "count": len(items),
                "sort": sort, "topic": topic, "inst": inst, "source": source,
                "country": country, "inst_kind": inst_kind, "cites_min": cites_min,
                "endorse": endorse,
                "near": near,
                "items": items, "facets": _facets(conn)}
    finally:
        conn.close()


def hero() -> dict:
    """首页顶部：几个"镇场子"的聚合数字。"""
    conn = connect()
    try:
        row = conn.execute(
            """SELECT COUNT(*) n, MAX(cited_by_count) max_cites,
                      AVG(works_count) avg_works
               FROM people WHERE status='published'""").fetchone()
        top_inst = conn.execute(
            """SELECT last_inst, COUNT(*) n FROM people
               WHERE status='published' AND last_inst IS NOT NULL
               GROUP BY last_inst ORDER BY n DESC LIMIT 6""").fetchall()
        return {"experts": row["n"], "max_cites": row["max_cites"],
                "avg_works": round(row["avg_works"] or 0, 1),
                "top_insts": [dict(r) for r in top_inst]}
    finally:
        conn.close()
