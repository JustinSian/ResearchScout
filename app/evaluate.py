"""评测：在同一批 gold 查询上比较 6 种检索配置。

指标：
  Recall@10 —— 期望集合里被检索到的比例
  MRR@10    —— 第一个正确结果的倒数名次
  nDCG@10   —— 二值相关性折损累计增益
并给每条配置的 **bootstrap 95% 置信区间**（1000 次重采样），避免"小样本上赢 0.02 就宣布更好"。
"""
from __future__ import annotations

import json
import math
import random

from app.db import connect
from app.search import (distribution_score_fusion, reciprocal_rank_fusion,
                        rerank_with_authority, search_dense, search_people,
                        search_works)
# 列举要比较的检索方法
CONFIGS = [
    ("bm25", {"mode": "bm25"}),
    ("dense", {"mode": "dense"}),
    ("rrf_k2", {"mode": "rrf", "k": 2}),
    ("rrf_k60", {"mode": "rrf", "k": 60}),
    ("dbsf", {"mode": "dbsf"}),
    # 权威性先验：λ 越大越偏向高被引（动机见 search.rerank_with_authority）
    ("bm25_a05", {"mode": "bm25", "lam": 0.5}),
    ("bm25_a10", {"mode": "bm25", "lam": 1.0}),
    ("bm25_a20", {"mode": "bm25", "lam": 2.0}),
    ("bm25_a40", {"mode": "bm25", "lam": 4.0}),
    ("rrf60_a10", {"mode": "rrf", "k": 60, "lam": 1.0}),
    ("rrf60_a20", {"mode": "rrf", "k": 60, "lam": 2.0}),
    ("bm25_a80", {"mode": "bm25", "lam": 8.0}),
]


def run_config(cfg: dict, q: str, kind: str, limit: int = 10) -> list[int]:
    lam = float(cfg.get("lam") or 0.0)
    if cfg["mode"] == "bm25":
        hits = (search_people(q, limit=100) if kind == "people"
                else search_works(q, limit=100))
    elif cfg["mode"] == "dense":
        hits = search_dense(q, kind=kind, limit=limit)
    else:
        lex = (search_people(q, limit=100) if kind == "people"
               else search_works(q, limit=100))
        dense = search_dense(q, kind=kind, limit=100)
        if cfg["mode"] == "dbsf":
            fused = distribution_score_fusion([lex, dense])
        else:
            fused = reciprocal_rank_fusion([lex, dense], k=cfg["k"])
        hits = sorted(fused.items(), key=lambda kv: -kv[1])[:100]
    if lam > 0:
        hits = rerank_with_authority(hits, lam=lam, kind=kind, limit=limit)
    return [int(i) for i, _s in hits[:limit]]

# 计算三个指标 recall\MRR\nDCG
def metrics(ranked: list[int], expected: set[int]) -> tuple[float, float, float]:
    hits = [i for i, doc in enumerate(ranked, 1) if doc in expected]
    recall = len({doc for doc in ranked if doc in expected}) / max(len(expected), 1)
    mrr = 1.0 / hits[0] if hits else 0.0
    dcg = sum(1.0 / math.log2(i + 1) for i in hits)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(expected), 10) + 1))
    return recall, mrr, (dcg / ideal if ideal else 0.0)

# 估算平均分的不确定性
def bootstrap_ci(values: list[float], n: int = 1000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def main() -> None:
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "eval/gold.json"
    gold = json.load(open(path, encoding="utf-8"))
    print(f"gold {len(gold)} 条  ({path})")
    rows = []
    for name, cfg in CONFIGS:
        per_q: list[list[float]] = []
        by_tier: dict[str, list[list[float]]] = {}
        for g in gold:
            ranked = run_config(cfg, g["query"], g["kind"])
            m = metrics(ranked, set(g["expected"]))
            per_q.append(list(m))
            by_tier.setdefault(g["tier"], []).append(list(m))
        def avg(idx: int, vals) -> float:
            return sum(v[idx] for v in vals) / max(len(vals), 1)
        nd = [v[2] for v in per_q]
        lo, hi = bootstrap_ci(nd)
        rows.append((name, avg(0, per_q), avg(1, per_q), avg(2, per_q), lo, hi, by_tier))
        print(f"  {name:<8} R@10 {rows[-1][1]:.3f}  MRR {rows[-1][2]:.3f}  "
              f"nDCG {rows[-1][3]:.3f}  CI[{lo:.3f},{hi:.3f}]")

    print("\n按查询类型看 nDCG@10：")
    tiers = ["name", "institution", "topic", "collaborator"]
    print("  配置      " + "".join(f"{t:<14}" for t in tiers))
    for name, _r, _m, _n, _lo, _hi, by_tier in rows:
        line = f"  {name:<9}"
        for t in tiers:
            vals = by_tier.get(t) or []
            line += f"{sum(v[2] for v in vals)/max(len(vals),1):<14.3f}"
        print(line)

    out = "eval/m2_results" + ("_alt" if "alt" in path else "") + ".json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump([{"config": r[0], "recall@10": round(r[1], 4), "mrr@10": round(r[2], 4),
                    "ndcg@10": round(r[3], 4), "ci_low": round(r[4], 4),
                    "ci_high": round(r[5], 4),
                    "by_tier": {t: round(sum(v[2] for v in vs)/max(len(vs),1), 4)
                                for t, vs in r[6].items()}}
                   for r in rows], fh, ensure_ascii=False, indent=1)
    print("\n写出 eval/m2_results.json")


if __name__ == "__main__":
    main()
