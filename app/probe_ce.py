"""探针：cross-encoder 能否分开"有答案 / 没答案"的查询。

对每个查询取 BM25 前 5 个候选人物的文档（姓名+机构+主题+代表作），
用 CE 打分取最大，看 GOOD / BAD 两组是否可分。
"""
from __future__ import annotations

import json

from app.db import connect
from app.rerank_ce import max_score, scores
from app.search import search_people

GOOD = ["graph neural network", "transformer", "dementia", "cancer immunotherapy",
        "reinforcement learning", "protein structure", "image segmentation",
        "drug discovery", "speech recognition", "recommender system"]
BAD = ["quantum gravity phenology", "medieval icelandic poetry", "zzz qwerty wibble",
       "underwater basket weaving", "phlogiston thermodynamics",
       "19th century violin making", "best pizza in naples", "how to train a puppy",
       "asteroid mining economics", "baroque architecture"]


def person_doc(conn, pid: int) -> str:
    r = conn.execute("SELECT name,last_inst,topics FROM people WHERE pid=?", (pid,)).fetchone()
    if not r:
        return ""
    papers = conn.execute(
        """SELECT w.title FROM works w JOIN work_authors wa ON wa.wid=w.wid
           WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 5""", (pid,)).fetchall()
    return (f"{r['name']}. {r['last_inst'] or ''}. "
            f"{' '.join(json.loads(r['topics'] or '[]')[:8])}. "
            f"{' '.join((p['title'] or '')[:100] for p in papers)}")


def run(q: str, conn) -> float:
    hits = search_people(q, limit=5)
    docs = [person_doc(conn, pid) for pid, _s in hits]
    docs = [d for d in docs if d]
    return max_score(q, docs) if docs else 0.0


def main() -> None:
    conn = connect()
    print(f"{'query':<34}{'CE_max':>9}")
    good_vals, bad_vals = [], []
    for label, qs, bucket in (("GOOD", GOOD, None), ("BAD", BAD, None)):
        print(f"-- {label} --")
        for q in qs:
            v = run(q, conn)
            print(f"{q[:32]:<34}{v:>9.3f}")
            (good_vals if label == "GOOD" else bad_vals).append(v)
    conn.close()
    print(f"\nGOOD min {min(good_vals):.3f} / max {max(good_vals):.3f}")
    print(f"BAD  min {min(bad_vals):.3f} / max {max(bad_vals):.3f}")
    gap = min(good_vals) - max(bad_vals)
    print(f"可分间隔: {gap:+.3f}（>0 表示存在一个阈值能完全分开）")


if __name__ == "__main__":
    main()
