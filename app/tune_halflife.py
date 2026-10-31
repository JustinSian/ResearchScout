"""半衰期扫描：PageRank 时间衰减的 tau 到底该取多少。

做法：对每个 tau 重算"偏好随机跳转"版 PageRank，看两个指标——

1. `top100_recent_share`：PageRank 前 100 名里，近 5 年论文的占比；
2. `top100_median_year`：前 100 名的年份中位数；
3. 对照 `base_recent_share`：库内全部论文的近 5 年占比（自然基线）。

**不自动选 tau**：我们没有"正确的新旧偏好"这个真值，自动挑一个数只会变成
"挑了个好看的数"。这里只把关系量出来，默认值仍取 8，并在文档里说明理由。

用法：python -m app.tune_halflife [--taus 4,8,12]
"""
from __future__ import annotations

import argparse
import statistics

from app.db import connect
from app.rank import pagerank


def sweep(taus: list[float], top_n: int = 100, recent_years: int = 5) -> list[dict]:
    conn = connect()
    try:
        nodes = [r[0] for r in conn.execute("SELECT wid FROM works")]
        edges = [(r[0], r[1]) for r in conn.execute(
            "SELECT wid, ref_wid FROM work_refs WHERE ref_wid IS NOT NULL")]
        years = {r[0]: r[1] for r in conn.execute("SELECT wid, year FROM works")}
    finally:
        conn.close()

    known = [y for y in years.values() if isinstance(y, int) and y > 1800]
    newest = max(known) if known else 2025
    cutoff = newest - recent_years
    base_recent = sum(1 for y in known if y > cutoff) / max(len(known), 1)

    out = []
    for tau in taus:
        weights = {}
        for w in nodes:
            y = years.get(w)
            age = 12 if (not isinstance(y, int) or y <= 1800) else max(0, newest - y)
            weights[w] = 0.5 ** (age / tau)
        scores = pagerank(nodes, edges, weights=weights)
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:top_n]
        ys = [years.get(w) for w, _ in top if isinstance(years.get(w), int)]
        out.append({
            "tau": tau,
            "top_recent_share": round(sum(1 for y in ys if y > cutoff) / max(len(ys), 1), 4),
            "top_median_year": int(statistics.median(ys)) if ys else None,
            "base_recent_share": round(base_recent, 4),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--taus", default="4,6,8,12,20")
    ap.add_argument("--top", type=int, default=100)
    a = ap.parse_args()
    taus = [float(x) for x in a.taus.split(",") if x.strip()]
    rows = sweep(taus, top_n=a.top)
    print(f"  {'tau':>5} {'前100近5年占比':>14} {'前100年份中位数':>16} {'全库近5年占比':>14}")
    for r in rows:
        print(f"  {r['tau']:>5} {r['top_recent_share']:>14.1%} "
              f"{str(r['top_median_year']):>16} {r['base_recent_share']:>14.1%}")


if __name__ == "__main__":
    main()
