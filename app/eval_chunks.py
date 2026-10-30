"""chunk 级 vs 文档级 vs 两路融合（同 gold set，按 tier 看）。

第一轮结论：chunk 检索把 topic 从 0.037 拉到 0.219，但 name 从 1.000 掉到 0.133。
所以正确做法不是"替换"，而是**路由或融合**——这里验证融合。
"""
from __future__ import annotations

import json

from app.evaluate import bootstrap_ci, metrics
from app.search import reciprocal_rank_fusion, search_people
from app.search_chunks import search_people_via_chunks

TIERS = ["name", "institution", "topic", "collaborator",
         "institution_random", "topic_random"]


def _people(q: str):
    return [pid for pid, _s in search_people(q, limit=50)]


def _chunks(q: str):
    return [pid for pid, _s in search_people_via_chunks(q, limit=50, chunk_limit=80)]


def _fused(q: str):
    a = search_people(q, limit=50)
    b = search_people_via_chunks(q, limit=50, chunk_limit=80)
    fused = reciprocal_rank_fusion([a, b], k=60)
    return [pid for pid, _s in sorted(fused.items(), key=lambda kv: -kv[1])[:10]]


def main() -> None:
    for gold_file in ("eval/gold.json", "eval/gold_alt.json"):
        gold = json.load(open(gold_file, encoding="utf-8"))
        print(f"\n== {gold_file}（{len(gold)} 条） ==")
        header = "".join(f"{t[:12]:<14}" for t in TIERS)
        print(f"  {'配置':<14}{header}总体nDCG")
        rows = {}
        for name, fn in (("people", _people), ("chunks", _chunks), ("fused_rrf", _fused)):
            by_tier: dict[str, list[float]] = {}
            allv: list[float] = []
            for g in gold:
                ranked = fn(g["query"])[:10]
                _r, _m, nd = metrics(ranked, set(g["expected"]))
                by_tier.setdefault(g["tier"], []).append(nd)
                allv.append(nd)
            rows[name] = allv
            line = f"  {name:<14}"
            for t in TIERS:
                v = by_tier.get(t) or []
                line += f"{(sum(v)/len(v) if v else 0):<14.3f}"
            lo, hi = bootstrap_ci(allv)
            print(f"{line}{sum(allv)/len(allv):.3f} CI[{lo:.3f},{hi:.3f}]")

        d1 = [y - x for x, y in zip(rows["people"], rows["chunks"])]
        d2 = [y - x for x, y in zip(rows["people"], rows["fused_rrf"])]
        for label, d in (("chunks-people", d1), ("fused-people", d2)):
            lo, hi = bootstrap_ci(d)
            verdict = "显著提升" if lo > 0 else ("不显著" if hi > 0 else "显著下降")
            print(f"  {label:<14}{sum(d)/len(d):+.3f} CI[{lo:+.3f},{hi:+.3f}] {verdict}")


if __name__ == "__main__":
    main()
