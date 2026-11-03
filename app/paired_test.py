"""配对检验：两个配置在同一批 query 上的差异，是否超出噪声。

为什么需要它：60 条 query 上 R@10 的差 4.7pt ≈ 1 个标准误，
不配对检验就把它归因于"IDF 漂移"是把噪声当信号。

提供两个口径：
  1. 精确 McNemar：只看"一个对、一个错"的翻转对，二项检验；
  2. 配对 bootstrap：对 query 重采样，给 ΔR@10 的 95% 区间。

用法：
  python -m app.paired_test rows_a.jsonl rows_b.jsonl
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path


def load(path: str) -> dict[str, dict]:
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return rows


def exact_mcnemar(b: int, c: int) -> float:
    """双侧精确 McNemar（二项，p=0.5）。b/c 是不一致对。"""
    n = b + c
    if n == 0:
        return 1.0
    from math import comb
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def bootstrap_ci(a: list[float], b: list[float], iters: int = 10000,
                 seed: int = 7) -> tuple[float, float]:
    rnd = random.Random(seed)
    n = len(a)
    diffs = []
    for _ in range(iters):
        idx = [rnd.randrange(n) for _ in range(n)]
        diffs.append(sum(a[i] - b[i] for i in idx) / n)
    diffs.sort()
    return diffs[int(iters * 0.025)], diffs[int(iters * 0.975)]


def compare(path_a: str, path_b: str) -> dict:
    A, B = load(path_a), load(path_b)
    ids = sorted(set(A) & set(B))
    ra = [A[i]["r10"] for i in ids]
    rb = [B[i]["r10"] for i in ids]
    b = sum(1 for i in ids if A[i]["r10"] > 0 and B[i]["r10"] == 0)   # A 对 B 错
    c = sum(1 for i in ids if A[i]["r10"] == 0 and B[i]["r10"] > 0)   # A 错 B 对
    lo, hi = bootstrap_ci(ra, rb)
    return {
        "n": len(ids),
        "r10_a": round(sum(ra) / len(ids), 4),
        "r10_b": round(sum(rb) / len(ids), 4),
        "delta": round((sum(ra) - sum(rb)) / len(ids), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "discordant": {"a_only": b, "b_only": c},
        "mcnemar_p": round(exact_mcnemar(b, c), 4),
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python -m app.paired_test A.jsonl B.jsonl")
        raise SystemExit(2)
    print(json.dumps(compare(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
