from app.agent import tool_find_expert
from app.config import CE_REFUSE
from app.rerank_ce import scores
from app.search import _fts_query, search_people, search_works

q = "找研究 graph neural network 的专家"
print("ce_refuse:", CE_REFUSE)
print("fts(works):", _fts_query(q))
print("fts(people):", _fts_query(q))
w = search_works(q, limit=10)
print("works hits:", len(w), w[:3])
p = search_people(q, limit=5)
print("people hits:", len(p), p[:3])
r = tool_find_expert(q)
print("tool result count:", r["count"], "ce_max:", r.get("ce_max"))
for it in (r["items"] or [])[:3]:
    print("   ", it["name"], it["works"], it["match"])
ws = [wid for wid, _ in w]
if ws:
    from app.db import connect
    conn = connect()
    docs = []
    for wid in ws[:5]:
        row = conn.execute("SELECT title,abstract FROM works WHERE wid=?", (wid,)).fetchone()
        docs.append(f"{row['title']}. {(row['abstract'] or '')[:200]}")
    print("CE on top5 works:", [round(x, 3) for x in scores(q, docs)])
    conn.close()
