"""对比两种候选文本构造的 CE 可分性。

A) 全局检索论文 → title+abstract
B) 检索到的人 → 其 top-10 代表作的标题拼接
看哪种能让 "graph neural network" 这类查询拿到高分。
"""
from __future__ import annotations

import json

from app.config import CE_REFUSE
from app.db import connect
from app.rerank_ce import scores
from app.search import search_people, search_works

GOOD = ["graph neural network", "transformer", "dementia", "cancer immunotherapy",
        "image segmentation", "drug discovery", "speech recognition"]
BAD = ["quantum gravity phenology", "medieval icelandic poetry",
       "underwater basket weaving", "baroque architecture"]


def doc_works_a(q: str, conn) -> tuple[list[str], list[str]]:
    titles, docs = [], []
    for wid, _s in search_works(q, limit=10):
        r = conn.execute("SELECT title,abstract FROM works WHERE wid=?", (wid,)).fetchone()
        if r:
            titles.append((r["title"] or "")[:60])
            docs.append(f"{r['title']}. {(r['abstract'] or '')[:400]}")
    return titles, docs


def doc_people_b(q: str, conn) -> tuple[list[str], list[str]]:
    names, docs = [], []
    for pid, _s in search_people(q, limit=5):
        r = conn.execute("SELECT name FROM people WHERE pid=?", (pid,)).fetchone()
        papers = conn.execute(
            """SELECT w.title FROM works w JOIN work_authors wa ON wa.wid=w.wid
               WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 10""", (pid,)).fetchall()
        names.append(r["name"] if r else str(pid))
        docs.append(" ".join((p["title"] or "")[:120] for p in papers))
    return names, docs


def main() -> None:
    conn = connect()
    print(f"threshold = {CE_REFUSE}")
    for label, qs in (("GOOD", GOOD), ("BAD", BAD)):
        print(f"\n-- {label} --")
        for q in qs:
            ta, da = doc_works_a(q, conn)
            nb, db = doc_people_b(q, conn)
            sa = scores(q, da) if da else []
            sb = scores(q, db) if db else []
            print(f"{q[:26]:<28} A(top论文) {max(sa) if sa else 0:.3f} "
                  f"| B(top人物代表作) {max(sb) if sb else 0:.3f}")
            if label == "GOOD" and sa and max(sa) < CE_REFUSE:
                print(f"     A 的候选: {ta[:3]}")
                print(f"     B 的候选: {nb[:3]}")
    conn.close()


if __name__ == "__main__":
    main()
