"""专家发现评测：用真实的 OpenAlex 概念标签当金标，算 recall@k / MRR。

评测的是**产品实际走的那条路**：`tool_find_expert`（论文检索 → 聚合到作者），
不是另写一条"更好看的"路径。

指标：
  recall@5 / recall@10   金标里前 k 个结果覆盖了多少
  mrr                    第一个金标出现的位置的倒数（越接近 1 越好）
  coverage               完全没命中的查询比例
  latency p50/p95        检索层耗时

用法：
  python -m app.build_expert_gold     # 生成 eval/expert_gold.jsonl（需要完整库）
  python -m app.eval_experts          # 跑评测
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

GOLD = Path(__file__).resolve().parents[1] / "eval" / "expert_gold.jsonl"


def load_gold(path: Path | None = None) -> list[dict]:
    rows = []
    for line in (path or GOLD).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _recall(hit: list[int], gold: set[int], k: int) -> float:
    if not gold:
        return 0.0
    return len(set(hit[:k]) & gold) / len(gold)


def _mrr(hit: list[int], gold: set[int]) -> float:
    for i, pid in enumerate(hit, 1):
        if pid in gold:
            return 1.0 / i
    return 0.0


def run(cases: list[dict], limit: int = 10, log=print) -> dict:
    from app.agent import tool_find_expert

    rows = []
    for c in cases:
        gold = set(int(x) for x in (c.get("gold_pids") or []))
        t0 = time.perf_counter()
        try:
            res = tool_find_expert(c["query"], limit=limit)
        except Exception as exc:
            res = {"items": [], "error": f"{type(exc).__name__}: {exc}"}
        ms = (time.perf_counter() - t0) * 1000
        hit = [int(it["pid"]) for it in (res.get("items") or []) if it.get("pid")]
        row = {"id": c["id"], "query": c["query"], "n_gold": len(gold),
               "r5": round(_recall(hit, gold, 5), 3), "r10": round(_recall(hit, gold, limit), 3),
               "mrr": round(_mrr(hit, gold), 3), "ms": round(ms, 1),
               "mode": res.get("mode") or "ce"}
        rows.append(row)
        flag = "✓" if row["r5"] > 0 else "✗"
        log(f"  {flag} {row['id']:<6} {row['query'][:24]:<26} "
            f"R@5 {row['r5']:.2f} R@10 {row['r10']:.2f} MRR {row['mrr']:.2f} "
            f"{row['ms']:>6.1f}ms [{row['mode']}]")

    n = len(rows) or 1
    ms = [r["ms"] for r in rows]
    summary = {
        "cases": len(rows),
        "recall@5": round(sum(r["r5"] for r in rows) / n, 4),
        "recall@10": round(sum(r["r10"] for r in rows) / n, 4),
        "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
        "coverage": round(sum(1 for r in rows if r["r10"] > 0) / n, 4),
        "latency_p50": round(statistics.median(ms), 1) if ms else 0,
        "latency_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "zero_ids": [r["id"] for r in rows if r["r10"] == 0],
    }
    return {"summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--file", default="")
    a = ap.parse_args()
    cases = load_gold(Path(a.file) if a.file else None)
    res = run(cases, limit=a.limit, log=(lambda *_: None) if a.json else print)
    s = res["summary"]
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print()
    print(f"  recall@5 {s['recall@5']:.1%} · recall@10 {s['recall@10']:.1%} · "
          f"MRR {s['mrr']:.3f} · 覆盖 {s['coverage']:.0%} · "
          f"p50 {s['latency_p50']}ms")
    if s["zero_ids"]:
        print("  完全未命中: " + ", ".join(s["zero_ids"]))


if __name__ == "__main__":
    main()
