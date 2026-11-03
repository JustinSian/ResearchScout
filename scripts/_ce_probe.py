import json, os, time
os.environ.setdefault("ST_CORE_MIX", "off")
from app import eval_experts
from app.eval_core import core_pids
from app.db import connect
from app.search import search_people
from app.rerank_ce import scores as ce_scores

gold = eval_experts.load_gold()
conn = connect()

def passages_for(pid, k=3):
    rows = conn.execute(
        """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
           WHERE wa.pid=? ORDER BY w.cites DESC LIMIT ?""", (pid, k)).fetchall()
    return [r["title"] for r in rows if r["title"]]

def eval_rank(by="cites", limit=10):
    from app.agent import _fts_safe
    import re
    r5=r10=rc=0.0; n=len(gold); cov=0; ncore=0; core_cov=0
    t0=time.time()
    for g in gold:
        con=g.get("concept") or ""
        related=set(int(x) for x in g.get("gold_pids") or [])
        core=set(core_pids(conn, con))
        hits=search_people(con, limit=60)
        cands=[]
        for pid,sc in hits:
            titles=passages_for(pid)
            cands.append((pid, titles, sc))
        if by=="cites":
            # 用 cited_by_count 排序（等同现状，log1p 单调）
            cands=sorted(cands, key=lambda c: -_cites(c[0]))
        elif by=="ce":
            titles=[t for _,ts,_ in cands for t in ts]  # placeholder
            # 逐个算，避免 batch 太多
            passages=[" ".join(ts[:3]) or " " for _,ts,_ in cands]
            scores=ce_scores(con, passages)
            cands=sorted(zip(cands, scores), key=lambda x: -x[1])
            cands=[c for c,_ in cands]
        top=[p for p,_,_ in cands[:limit]]
        r5+=len(set(top[:5])&related)/max(len(related),1)
        r10+=len(set(top)&related)/max(len(related),1)
        if set(top)&related: cov+=1
        if core:
            ncore+=1
            x=len(set(top)&core)/len(core)
            rc+=x
            if x>0: core_cov+=1
    print(f"  [{by}] R@5 {r5/n:.1%} R@10 {r10/n:.1%} 覆盖 {cov/n:.0%} | R_core {rc/max(ncore,1):.1%} 核心覆盖 {core_cov/max(ncore,1):.0%} · {time.time()-t0:.0f}s")

def _cites(pid):
    r=conn.execute("SELECT cited_by_count FROM people WHERE pid=?", (pid,)).fetchone()
    return int(r[0]) if r else 0

print("== CE 重排对照（55 概念）==")
eval_rank("cites")
eval_rank("ce")
conn.close()
