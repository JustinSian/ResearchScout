"""第二批盲审：从 525 条金标关系里随机抽 30 条，估计**金标纯度**。

和第一批的区别：
  第一批  每个概念抽 1 人，检查"金标构造成不成立"（发现 2 条错误）
  第二批  从全部关系里随机抽，用来估计总体纯度及置信区间

输出 eval/audit2/：review.html（匿名、打乱）、review.txt、mapping.json
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from app.build_audit import HTML, anonymize, write_page, write_text

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "expert_gold.jsonl"


def build(n: int, seed: int = 21) -> list[dict]:
    from app.db import connect

    gold = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    pairs = [(g["concept"], int(pid)) for g in gold for pid in (g.get("gold_pids") or [])]
    pairs = list(dict.fromkeys(pairs))
    rnd = random.Random(seed)
    picked = rnd.sample(pairs, min(n, len(pairs)))

    conn = connect()
    rows = []
    try:
        for concept, pid in picked:
            p = conn.execute(
                """SELECT p.pid, p.name, p.last_inst, p.cited_by_count, p.topics
                   FROM people p WHERE p.pid=?""", (pid,)).fetchone()
            if not p:
                continue
            try:
                tags = json.loads(p["topics"] or "[]")[:6]
            except Exception:
                tags = []
            titles = [r["title"] or "" for r in conn.execute(
                """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
                   WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 3""", (pid,))]
            rows.append({
                "row_id": f"G{concept}|{pid}",
                "query": concept,
                "name": p["name"],
                "institution": p["last_inst"] or "",
                "cited_by": p["cited_by_count"],
                "topics": " / ".join(tags),
                "top3_titles": " || ".join(t[:110] for t in titles),
                "label_is_expert": "",
                "note": "",
            })
    finally:
        conn.close()
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default="eval/audit2")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    rows = build(a.n)
    for r in rows:
        r["_source_ref"] = r["row_id"]
    rows, mapping = anonymize(rows, seed=77)
    random.Random(99).shuffle(rows)
    write_page(rows, out)
    write_text(rows, out / "review.txt")
    (out / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"  {len(rows)} 条 → {out / 'review.html'}")
    print(f"  私有映射 → {out / 'mapping.json'}")


if __name__ == "__main__":
    main()
