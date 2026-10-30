"""构造 gold set：真值来自结构化字段，而不是人工标注。

四类查询，各 25 条（共 100）：
  name          "find Yoshua Bengio"                 真值 = 该姓名的 person
  institution   "researchers at Google DeepMind"     真值 = last_inst 匹配的人（取被引前 5）
  topic         "experts in graph neural networks"   真值 = topics 含该词的人（取被引前 5）
  collaborator  "who works with Yoshua Bengio"       真值 = 合作边邻居（取权重前 5）

为什么这么做：手工标注 100 条成本高且主观；用结构化字段派生真值是**可复现**的，
而且能分别暴露"词面检索强 / 语义检索强 / 图谱检索强"的差异。
"""
from __future__ import annotations

import json
import random

from app.db import connect

OUT = "eval/gold.json"
N_PER_TIER = 25


def main() -> None:
    conn = connect()
    rng = random.Random(20261029)
    gold: list[dict] = []

    # 1) name
    people = conn.execute(
        """SELECT pid,name FROM people WHERE status='published' AND works_count>=3
           AND cited_by_count>=1000 ORDER BY cited_by_count DESC LIMIT 400""").fetchall()
    picked = rng.sample(people, N_PER_TIER)
    for r in picked:
        gold.append({"tier": "name", "query": f"find {r['name']}",
                     "kind": "people", "expected": [r["pid"]]})

    # 2) institution
    insts = conn.execute(
        """SELECT last_inst, COUNT(*) n FROM people
           WHERE status='published' AND last_inst IS NOT NULL AND works_count>=2
           GROUP BY last_inst HAVING n>=5 ORDER BY n DESC LIMIT 60""").fetchall()
    for r in rng.sample(insts, N_PER_TIER):
        rows = conn.execute(
            """SELECT pid FROM people WHERE status='published' AND last_inst=?
               AND works_count>=2 ORDER BY cited_by_count DESC LIMIT 5""",
            (r["last_inst"],)).fetchall()
        if len(rows) < 2:
            continue
        gold.append({"tier": "institution", "query": f"researchers at {r['last_inst']}",
                     "kind": "people", "expected": [x["pid"] for x in rows]})

    # 3) topic
    topics: dict[str, int] = {}
    for r in conn.execute("SELECT topics FROM people WHERE status='published'"):
        for t in json.loads(r["topics"] or "[]"):
            topics[t] = topics.get(t, 0) + 1
    hot = [t for t, n in sorted(topics.items(), key=lambda kv: -kv[1])[:80] if n >= 8]
    for t in rng.sample(hot, N_PER_TIER):
        rows = conn.execute(
            """SELECT pid FROM people WHERE status='published' AND topics LIKE ?
               ORDER BY cited_by_count DESC LIMIT 5""", (f'%"{t}"%',)).fetchall()
        if len(rows) < 2:
            continue
        gold.append({"tier": "topic", "query": f"experts in {t}",
                     "kind": "people", "expected": [x["pid"] for x in rows]})

    # 4) collaborator
    hubs = conn.execute(
        """SELECT p.pid,p.name,COUNT(*) n FROM people p JOIN edges e
             ON e.a=p.pid OR e.b=p.pid
           WHERE p.status='published' AND p.works_count>=3
           GROUP BY p.pid HAVING n>=5 ORDER BY n DESC LIMIT 80""").fetchall()
    for r in rng.sample(hubs, N_PER_TIER):
        rows = conn.execute(
            """SELECT CASE WHEN a=? THEN b ELSE a END AS other, weight FROM edges
               WHERE a=? OR b=? ORDER BY weight DESC LIMIT 5""",
            (r["pid"], r["pid"], r["pid"])).fetchall()
        if len(rows) < 2:
            continue
        gold.append({"tier": "collaborator", "query": f"who works with {r['name']}",
                     "kind": "people", "expected": [x["other"] for x in rows]})

    conn.close()
    import os
    os.makedirs("eval", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(gold, fh, ensure_ascii=False, indent=1)
    by_tier: dict[str, int] = {}
    for g in gold:
        by_tier[g["tier"]] = by_tier.get(g["tier"], 0) + 1
    print(f"gold 写出 {len(gold)} 条 → {OUT}  分布 {by_tier}")
    print("示例:", json.dumps(gold[0], ensure_ascii=False)[:140])


if __name__ == "__main__":
    main()
