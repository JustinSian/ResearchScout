"""构造"缺口主题"评测集：库里几乎没有专家、但该领域真实存在。

为什么不能从 `expert_gold` 里挑：那 60 个概念本来就是"库里有 ≥5 位标签"的主题，
天然不缺口。必须从**外部**找主题。

做法：
1. 从 OpenAlex 拉近期高被引论文（默认 3 页 × 100），收集它们携带的
   **细粒度主题**（`topics[].display_name`，如 "Niemann-Pick Disease"）；
2. 过滤：库里 published 人数 < 2（确实缺口）；
3. 构造金标池：这些主题下论文的作者，且作者 profile 的 x_concepts/topics
   **包含该主题**（独立于"标题匹配"这个生成器）；
4. 池子 ≥ 5 人的主题保留，固定种子采样。

输出 eval/gap_topics.jsonl：{id, topic, db_before, pool_size, gold_openalex_ids, gold_names}
"""
from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

from app.db import connect
from app.openalex import OpenAlex

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "gap_topics.jsonl"
_OA = OpenAlex()


def db_published_count(topic: str) -> int:
    conn = connect()
    try:
        return int(conn.execute(
            "SELECT COUNT(*) FROM people WHERE status='published' AND topics LIKE ?",
            (f"%{topic}%",)).fetchone()[0])
    finally:
        conn.close()


def collect_topics(pages: int = 3, per_page: int = 100) -> dict[str, list[dict]]:
    """近期高被引论文 → {主题: [works]}。"""
    topics: dict[str, list[dict]] = collections.defaultdict(list)
    cursor = "*"
    for _ in range(pages):
        data = _OA._get("/works", {
            "filter": "from_publication_date:2020-01-01",
            "sort": "cited_by_count:desc",
            "per_page": per_page, "cursor": cursor,
        })
        works = data.get("results") or []
        if not works:
            break
        for w in works:
            for t in (w.get("topics") or [])[:2]:
                name = (t.get("display_name") or "").strip()
                if name:
                    topics[name].append(w)
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return topics


def build_pool(topic: str, works: list[dict], max_profiles: int = 14) -> list[dict]:
    """金标池：作者 profile 的标签里含该主题。"""
    seen: dict[str, dict] = {}
    for w in works[:20]:
        for au in (w.get("authorships") or [])[:8]:
            a = au.get("author") or {}
            aid = a.get("id")
            if aid and aid not in seen:
                seen[aid] = {"id": aid, "name": a.get("display_name")}
    pool = []
    for aid, rec in list(seen.items())[:max_profiles]:
        short = aid.rsplit("/", 1)[-1]
        try:
            prof = _OA._get(f"/authors/{short}", {})
        except Exception:
            continue
        labels = [c.get("display_name") or "" for c in (prof.get("x_concepts") or [])]
        labels += [t.get("display_name") or "" for t in (prof.get("topics") or [])]
        if any(topic.lower() in l.lower() for l in labels):
            pool.append({"openalex_id": aid,
                         "name": prof.get("display_name") or rec["name"]})
    return pool


def build(limit: int = 20, min_pool: int = 5, seed: int = 7) -> list[dict]:
    topics = collect_topics()
    cands = [(t, ws) for t, ws in topics.items()
             if len(ws) >= 3 and db_published_count(t) < 2]
    cands.sort(key=lambda kv: -len(kv[1]))
    rnd = random.Random(seed)
    rnd.shuffle(cands)

    out = []
    for topic, works in cands:
        if len(out) >= limit:
            break
        try:
            pool = build_pool(topic, works)
        except Exception:
            continue
        if len(pool) < min_pool:
            continue
        out.append({
            "id": f"gap-{len(out) + 1:02d}",
            "topic": topic,
            "db_before": db_published_count(topic),
            "pool_size": len(pool),
            "gold_openalex_ids": [p["openalex_id"] for p in pool],
            "gold_names": [p["name"] for p in pool],
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-pool", type=int, default=5)
    a = ap.parse_args()
    rows = build(a.limit, a.min_pool)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    print(f"  写入 {len(rows)} 个缺口主题 → {OUT}")
    for r in rows[:10]:
        print(f"   {r['id']} {r['topic'][:38]:<40} 库内 {r['db_before']} | 池 {r['pool_size']}")


if __name__ == "__main__":
    main()
