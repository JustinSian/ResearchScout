"""M7：全文 chunk vs 摘要 chunk vs 人物索引。

三个口径都要看，否则会得出误导结论：
  1) 全库口径 —— 8000 篇里只有 2.4k 有全文，混合口径会被稀释
  2) 有全文子集口径 —— 只评"其代表作有全文"的专家，这才是全文的真实效果
  3) 两套 gold —— 按被引的真值 / 随机真值（前几轮已经证明它们会给出相反结论）
"""
from __future__ import annotations

import json

from app.db import connect
from app.evaluate import bootstrap_ci, metrics
from app.search import search_people
from app.search_chunks import search_people_via_chunks


def people_with_fulltext(conn) -> set[int]:
    rows = conn.execute(
        """SELECT DISTINCT wa.pid FROM work_text t
           JOIN work_authors wa ON wa.wid = t.wid""").fetchall()
    return {int(r["pid"]) for r in rows}


def run_variant(name, fn, gold, full_pids, restrict: bool):
    by_tier: dict[str, list[float]] = {}
    allv: list[float] = []
    skipped = 0
    for g in gold:
        expected = set(g["expected"])
        if restrict:
            expected = expected & full_pids
            if len(expected) < 2:
                skipped += 1
                continue
        ranked = [pid for pid, _s in fn(g["query"])][:10]
        _r, _m, nd = metrics(ranked, expected)
        by_tier.setdefault(g["tier"], []).append(nd)
        allv.append(nd)
    return {"name": name, "ndcg": sum(allv) / max(len(allv), 1), "n": len(allv),
            "skipped": skipped, "by_tier": {k: sum(v) / len(v) for k, v in by_tier.items()},
            "values": allv}


def main() -> None:
    conn = connect()
    full_pids = people_with_fulltext(conn)
    n_full_works = conn.execute("SELECT COUNT(*) FROM work_text").fetchone()[0]
    dist = {r["src"]: r["n"] for r in conn.execute(
        "SELECT src, COUNT(*) n FROM chunks GROUP BY src")}
    conn.close()
    print(f"全文论文 {n_full_works} 篇 / 覆盖作者 {len(full_pids)} 人 / chunk 分布 {dist}")

    variants = [
        ("people", lambda q: search_people(q, limit=10)),
        ("chunk", lambda q: search_people_via_chunks(q, limit=10, chunk_limit=80)),
    ]
    for gold_file in ("eval/gold_alt.json", "eval/gold.json"):
        gold = json.load(open(gold_file, encoding="utf-8"))
        for restrict in (False, True):
            label = "有全文子集" if restrict else "全库"
            print(f"\n== {gold_file} · {label} ==")
            for name, fn in variants:
                r = run_variant(name, fn, gold, full_pids, restrict)
                lo, hi = bootstrap_ci(r["values"])
                tiers = " ".join(f"{k[:8]}={v:.3f}" for k, v in sorted(r["by_tier"].items()))
                print(f"  {name:<7} nDCG {r['ndcg']:.3f} CI[{lo:.3f},{hi:.3f}] "
                      f"n={r['n']} 跳过 {r['skipped']} | {tiers}")


if __name__ == "__main__":
    main()
