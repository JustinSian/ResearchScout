"""清洗专家发现金标：移除已确认错误，并扫描"概念标签噪声"。

金标的生成方式是"OpenAlex 概念标签"，问题是标签会挂在**用过该工具/分类**的人身上
（盲审实测：`Association (psychology)` 抽到痴呆神经病学家、`Physical therapy` 抽到生物统计学家）。

本脚本做两件事：
  1. 移除人工确认的错误 (concept, pid) 对（blocklist）；
  2. 扫描全量：如果某人的代表作标题与概念关键词**零重叠**，标记为疑似噪声，
     输出 `eval/gold_flags.csv` 供二次抽查（不自动删除）。

用法：
  python -m app.clean_expert_gold --dry-run     # 只看会删多少、标多少
  python -m app.clean_expert_gold               # 写回 gold（先备份）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "expert_gold.jsonl"
BACKUP = ROOT / "eval" / "expert_gold.raw.jsonl"
FLAGS = ROOT / "eval" / "gold_flags.csv"

# 盲审确认错误：概念 → 该概念下被误收的 pid
BLOCKLIST = {
    # 盲审确认：概念标签挂错人，不是该领域研究者
    "Association (psychology)": [1612],    # David S. Knopman（痴呆神经病学家）
    "Physical therapy": [1423],             # D A Bloch（生物统计学家）
}

STOP = {"and", "the", "of", "for", "with", "using", "based", "analysis", "system",
        "systems", "model", "models", "data", "study", "research", "method",
        "methods", "approach", "theory", "application", "applications"}


def keywords(concept: str) -> list[str]:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]+", concept or "")]
    return [t for t in toks if t not in STOP]


def titles_of(conn, pid: int, limit: int = 50) -> list[str]:
    rows = conn.execute(
        """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
           WHERE wa.pid=? ORDER BY w.cites DESC LIMIT ?""", (int(pid), limit)).fetchall()
    return [r["title"] or "" for r in rows]


def scan(dry_run: bool = False) -> dict:
    from app.db import connect

    if not BACKUP.exists():
        shutil.copy(GOLD, BACKUP)
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]

    conn = connect()
    flags = []
    removed = 0
    out = []
    try:
        for g in rows:
            concept = g.get("concept") or g.get("query") or ""
            kws = keywords(concept)
            kept = []
            for pid in g.get("gold_pids") or []:
                pid = int(pid)
                if pid in BLOCKLIST.get(concept, []):
                    removed += 1
                    continue
                kept.append(pid)
                if not kws:
                    continue
                titles = " ".join(titles_of(conn, pid, 50)).lower()
                # 筛查用"全部论文"：只看前 5 篇会大量误报（实测 276 条）
                if titles and not any(k in titles for k in kws):
                    flags.append({"concept": concept, "pid": pid,
                                  "titles": " || ".join(t[:90] for t in titles_of(conn, pid, 3))})
            g["gold_pids"] = kept
            out.append(g)
    finally:
        conn.close()

    with FLAGS.open("w", encoding="utf-8", newline="") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=["concept", "pid", "titles"])
        w.writeheader()
        w.writerows(flags)

    if not dry_run:
        GOLD.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n",
                        encoding="utf-8")
    return {"concepts": len(out), "removed": removed, "flagged": len(flags),
            "dry_run": dry_run, "flags_file": str(FLAGS)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = scan(a.dry_run)
    print(f"  概念 {r['concepts']} 个 · 移除确认错误 {r['removed']} 条 · "
          f"疑似噪声（代表作零重叠）{r['flagged']} 条")
    print(f"  标记清单 → {r['flags_file']}" + ("（dry-run，未写回）" if r["dry_run"] else "（已写回金标）"))


if __name__ == "__main__":
    main()
