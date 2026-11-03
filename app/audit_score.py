"""盲审结果打分：精确率 + Wilson 置信区间 + 标注者一致性（kappa）。

输入：build_audit 导出的 CSV（含 label_is_expert 列，值：是/否/边界）。
边界不计入精确率分母，单独报告。

用法：
  python -m app.audit_score eval/audit/audit_labeled.csv
  python -m app.audit_score eval/audit/a.csv eval/audit/b.csv   # 两人标注，算 kappa
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

VALID = {"是", "否", "边界", "y", "n", "b", "yes", "no", "border"}


def load(path: str) -> dict[str, str]:
    out = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            v = (r.get("label_is_expert") or "").strip().lower()
            if v:
                out[r.get("row_id") or r.get("id") or ""] = v
    return out


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def score(labels: dict[str, str]) -> dict:
    vals = list(labels.values())
    yes = sum(1 for v in vals if v in ("是", "y", "yes"))
    no = sum(1 for v in vals if v in ("否", "n", "no"))
    border = sum(1 for v in vals if v in ("边界", "b", "border"))
    n = yes + no
    lo, hi = _wilson(yes, n)
    return {"labeled": len(vals), "yes": yes, "no": no, "border": border,
            "precision": round(yes / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def kappa(a: dict[str, str], b: dict[str, str]) -> float | None:
    ids = sorted(set(a) & set(b))
    if len(ids) < 5:
        return None
    cats = sorted({a[i] for i in ids} | {b[i] for i in ids})
    n = len(ids)
    po = sum(1 for i in ids if a[i] == b[i]) / n
    pe = sum((sum(1 for i in ids if a[i] == c) / n) *
             (sum(1 for i in ids if b[i] == c) / n) for c in cats)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m app.audit_score labeled.csv [second.csv]")
        raise SystemExit(2)
    a = load(sys.argv[1])
    res = score(a)
    print("  标注", res["labeled"], "条：是", res["yes"], "/ 否", res["no"], "/ 边界", res["border"])
    if res["precision"] is not None:
        print(f"  精确率 {res['precision']:.1%}  95% CI [{res['ci95'][0]:.1%}, {res['ci95'][1]:.1%}]")
    if len(sys.argv) > 2:
        b = load(sys.argv[2])
        k = kappa(a, b)
        print("  标注者一致性 kappa:", k if k is not None else "样本不足")
        # 各自分数
        for name, lab in (("A", a), ("B", b)):
            r = score(lab)
            print(f"   {name}: 精确率 {r['precision']:.1%}" if r["precision"] else f"   {name}: 无有效标注")


if __name__ == "__main__":
    main()
