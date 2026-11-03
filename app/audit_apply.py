"""把盲审答案合并回样本，分别算金标纯度与扩充精确率。

输入：
  eval/audit/review_answers.txt   标注者按文本版编号回复（N ... | 是/否/边界 | 依据）
  eval/audit/review.txt           编号 → 不透明 row_id 的顺序
  eval/audit/mapping.json         row_id → 原始 id（G=金标 / X=扩充）

输出：
  eval/audit/audit_labeled.csv    合并后的标注（含 kind 列，仅本地分析用）
  控制台：总体 / 金标 / 扩充 三组精确率 + Wilson 95% CI

用法：
  python -m app.audit_apply
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUD = ROOT / "eval" / "audit"


def parse_answers(path: Path) -> dict[int, tuple[str, str]]:
    """返回 {编号: (判定, 依据)}。"""
    out: dict[int, tuple[str, str]] = {}
    pat = re.compile(r"^\s*(\d+)\s+.*?\|\s*(是|否|边界)\s*\|\s*(.*)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            out[int(m.group(1))] = (m.group(2), m.group(3).strip())
    return out


def parse_order(path: Path) -> dict[int, str]:
    """从 review.txt 提取 编号 → row_id。"""
    out = {}
    pat = re.compile(r"^\s*(\d+)\.\s*\[(R[0-9a-f]+)\]")
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pat.match(line)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    import math
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def stats(rows: list[dict]) -> dict:
    yes = sum(1 for r in rows if r["label"] == "是")
    no = sum(1 for r in rows if r["label"] == "否")
    border = sum(1 for r in rows if r["label"] == "边界")
    n = yes + no
    lo, hi = wilson(yes, n)
    return {"n": len(rows), "yes": yes, "no": no, "border": border,
            "precision": round(yes / n, 4) if n else None,
            "ci95": [round(lo, 4), round(hi, 4)]}


def main() -> None:
    answers = parse_answers(AUD / "review_answers.txt")
    order = parse_order(AUD / "review.txt")
    mapping = json.loads((AUD / "mapping.json").read_text(encoding="utf-8"))

    rows = []
    for num, row_id in sorted(order.items()):
        label, note = answers.get(num, ("", ""))
        orig = mapping.get(row_id, "")
        kind = "gold" if orig.startswith("G") else ("expansion" if orig else "unknown")
        rows.append({"row_id": row_id, "num": num, "orig": orig, "kind": kind,
                     "label": label, "note": note})

    out = AUD / "audit_labeled.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "num", "orig", "kind",
                                          "label", "note"])
        w.writeheader()
        w.writerows(rows)

    missing = [r["num"] for r in rows if not r["label"]]
    groups = {
        "总体": rows,
        "金标（20 条）": [r for r in rows if r["kind"] == "gold"],
        "扩充（21 条）": [r for r in rows if r["kind"] == "expansion"],
    }
    print(f"  解析 {len(answers)} 条答案 / 样本 {len(rows)} 条"
          + (f"，缺 {missing}" if missing else "，无遗漏"))
    for name, g in groups.items():
        s = stats(g)
        line = f"  {name}: 是 {s['yes']} · 边界 {s['border']} · 否 {s['no']}"
        if s["precision"] is not None:
            line += (f" 严格 {s['precision']:.1%} "
                     f"(95% CI {s['ci95'][0]:.1%}–{s['ci95'][1]:.1%})")
        # 宽松口径：边界算命中（"相关但不是核心"）
        yes_b = s["yes"] + s["border"]
        n_b = yes_b + s["no"]
        if n_b:
            lo, hi = wilson(yes_b, n_b)
            line += f" · 宽松 {yes_b / n_b:.1%} (CI {lo:.1%}–{hi:.1%})"
        print(line)
    print(f"  合并结果 → {out}")


if __name__ == "__main__":
    main()
