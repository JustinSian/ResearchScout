"""数据健康度：把"这次踩过的坑"变成可监控的指标。

每一项对应一次真实事故或一条产品约束：
  - 网络背书占比 → M12/M13：扩张时靠背书灌人，超过 5% 说明门槛该重新校准
  - 无作者论文   → M11：作者-论文错位时会出现大量空作者
  - 重复 openalex_id → M11：UPSERT 误用 lastrowid 的副产品
  - 悬空边       → 合并人物后可能残留
  - FTS 行数不一致 → 增量更新 FTS 时漏写
  - 空主题占比   → 外部导入记录常见

用法：GET /health/trust 看详情；/stats 里带一个 summary。
"""
from __future__ import annotations

from app.db import connect

LIMITS = {
    "network_ratio": 0.05,      # 网络背书不超过发布集 5%
    "works_without_authors": 0.05,   # 无作者论文（书/报告）应低于 5%
    "empty_topics": 0.35,       # 没有主题的人太多说明采集/富化有缺口
}


def _pct(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def trust_health() -> dict:
    conn = connect()
    try:
        def one(sql: str, args: tuple = ()) -> int:
            return int(conn.execute(sql, args).fetchone()[0])

        published = one("SELECT COUNT(*) FROM people WHERE status='published'")
        network = one("SELECT COUNT(*) FROM people WHERE status='published' AND endorse='network'")
        works = one("SELECT COUNT(*) FROM works")
        no_authors = one("""SELECT COUNT(*) FROM works w WHERE NOT EXISTS
                            (SELECT 1 FROM work_authors wa WHERE wa.wid=w.wid)""")
        dup_oid = one("""SELECT COUNT(*) FROM (SELECT openalex_id FROM works
                          WHERE openalex_id IS NOT NULL GROUP BY openalex_id
                          HAVING COUNT(*) > 1)""")
        dangling = one("""SELECT COUNT(*) FROM edges e WHERE
                            NOT EXISTS (SELECT 1 FROM people p WHERE p.pid = e.a)
                         OR NOT EXISTS (SELECT 1 FROM people p WHERE p.pid = e.b)""")
        people = one("SELECT COUNT(*) FROM people")
        fts = one("SELECT COUNT(*) FROM people_fts")
        empty_topics = one("SELECT COUNT(*) FROM people WHERE status='published' AND (topics IS NULL OR topics='[]')")

        checks = [
            {
                "name": "network_endorse_ratio",
                "label": "网络背书占发布集比例",
                "value": _pct(network, published),
                "limit": LIMITS["network_ratio"],
                "count": network, "total": published,
                "status": "ok" if _pct(network, published) <= LIMITS["network_ratio"] else "warn",
                "hint": "超过上限说明在靠'和名人合作过'灌人，应重新校准门槛",
            },
            {
                "name": "works_without_authors",
                "label": "无作者的论文比例",
                "value": _pct(no_authors, works),
                "limit": LIMITS["works_without_authors"],
                "count": no_authors, "total": works,
                "status": "ok" if _pct(no_authors, works) <= LIMITS["works_without_authors"] else "warn",
                "hint": "OpenAlex 侧的书/报告本来就没有作者；异常升高说明作者关联又错位了",
            },
            {
                "name": "duplicate_openalex_id",
                "label": "重复的 openalex_id",
                "value": dup_oid, "limit": 0, "count": dup_oid, "total": works,
                "status": "ok" if dup_oid == 0 else "warn",
                "hint": "UPSERT 误用 lastrowid 会留下重复行",
            },
            {
                "name": "dangling_edges",
                "label": "悬空边（指向不存在的人）",
                "value": dangling, "limit": 0, "count": dangling, "total": 0,
                "status": "ok" if dangling == 0 else "warn",
                "hint": "合并人物后可能残留，需要清理",
            },
            {
                "name": "fts_row_mismatch",
                "label": "FTS 行数与人物数之差",
                "value": abs(people - fts), "limit": 0, "count": fts, "total": people,
                "status": "ok" if people == fts else "warn",
                "hint": "增量更新 FTS 时漏写会导致某些人搜不到",
            },
            {
                "name": "empty_topics",
                "label": "发布专家中无主题的比例",
                "value": _pct(empty_topics, published),
                "limit": LIMITS["empty_topics"],
                "count": empty_topics, "total": published,
                "status": "ok" if _pct(empty_topics, published) <= LIMITS["empty_topics"] else "warn",
                "hint": "外部导入记录常见；过高会让主题筛选失效",
            },
        ]
        bad = [c for c in checks if c["status"] != "ok"]
        return {"ok": not bad, "warnings": len(bad),
                "network_ratio": _pct(network, published),
                "limit": LIMITS["network_ratio"], "checks": checks}
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(trust_health(), ensure_ascii=False, indent=1))
