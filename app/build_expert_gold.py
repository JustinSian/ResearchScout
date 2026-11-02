"""从库里真实的 OpenAlex 概念标签构造"专家发现"评测集。

为什么要自建而不是只用现成数据集：
- BEIR/S2AND 评的是**文档检索**和**同名消歧**，不是"这个领域该找谁"；
- 我们库里有 4.1 万人的概念标签（OpenAlex concepts），可以直接问
  "这个概念的专家是谁"，用标签当金标，用检索结果算 recall。

构造规则（写死，可审计）：
1. 只取频次 5~120 的概念（太少没有可比性，太多是 "Medicine" 这种泛概念）；
2. 排除泛学科词（Medicine/Biology/Physics/...），否则标签没有区分度；
3. 每个概念的金标 = 该标签下被引最高的至多 10 人；
4. 采样 60 个概念，固定随机种子，保证可复现。

输出：eval/expert_gold.jsonl
  {"id","query","concept","gold_pids":[...],"n_gold"}
"""
from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

from app.db import connect

OUT = Path(__file__).resolve().parents[1] / "eval" / "expert_gold.jsonl"

GENERIC = {
    "Medicine", "Biology", "Physics", "Chemistry", "Computer science", "Mathematics",
    "Psychology", "Engineering", "Sociology", "Political science", "Economics",
    "Geography", "Geology", "Materials science", "Environmental science", "Disease",
    "Internal medicine", "Genetics", "Gene", "Genome", "Chemistry", "Organic chemistry",
    "Artificial intelligence", "Machine learning", "Statistics", "Biochemistry",
}


def build(min_n: int = 5, max_n: int = 120, limit: int = 60, seed: int = 42) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT pid, name, name_zh, topics, cited_by_count FROM people "
            "WHERE topics IS NOT NULL AND topics != '' AND status='published'").fetchall()
    finally:
        conn.close()

    concept_people: dict[str, list[tuple[int, int, str]]] = collections.defaultdict(list)
    for r in rows:
        try:
            topics = json.loads(r["topics"])
        except Exception:
            continue
        for t in set(topics or []):
            if t and t not in GENERIC:
                concept_people[t].append(
                    (int(r["pid"]), int(r["cited_by_count"] or 0),
                     r["name_zh"] or r["name"] or ""))

    cands: list[tuple[str, list[tuple[int, int, str]]]] = []
    for t, people in concept_people.items():
        if min_n <= len(people) <= max_n:
            cands.append((t, people))
    cands.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    rnd = random.Random(seed)
    picked = rnd.sample(cands, min(limit, len(cands)))

    out = []
    for i, (t, people) in enumerate(picked, 1):
        people_sorted = sorted(people, key=lambda x: -x[1])[:10]
        out.append({
            "id": f"ex-{i:02d}",
            "query": t,
            "concept": t,
            "gold_pids": [p[0] for p in people_sorted],
            "gold_names": [p[2] for p in people_sorted],
            "n_gold": len(people_sorted),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=5)
    ap.add_argument("--max", type=int, default=120)
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()
    rows = build(a.min, a.max, a.limit)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"  写入 {len(rows)} 条 → {OUT}")
    for r in rows[:5]:
        print(f"   {r['id']} {r['query'][:30]:<32} 金标 {r['n_gold']} 人")


if __name__ == "__main__":
    main()
