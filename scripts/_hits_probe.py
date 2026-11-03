import math, os, re, time
os.environ["ST_CORE_MIX"] = "off"
from app import eval_experts
from app.eval_core import core_pids, keywords
from app.db import connect
from app.search import search_people
from app.agent import _fts_safe

gold = eval_experts.load_gold()
conn = connect()

def hits_of(concept, pids):
    kws = [t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", _fts_safe(concept)) if len(t) > 3][:4]
    if not kws or not pids:
        return {}
    like = " OR ".join(["lower(w.title) LIKE ?"] * len(kws))
    args = [f"%{k}%" for k in kws]
    marks = ",".join("?" * len(pids))
    rows = conn.execute(
        f"""SELECT wa.pid, COUNT(*) AS h FROM work_authors wa JOIN works w ON w.wid=wa.wid
            WHERE ({like}) AND wa.pid IN ({marks}) GROUP BY wa.pid""",
        (*args, *pids)).fetchall()
    return {int(r["pid"]): int(r["h"]) for r in rows}

def run(mode, w=1.0):
    r5 = r10 = rc = 0.0; n = len(gold); core_n = 0; core_cov = 0; t0 = time.time()
    for g in gold:
        con = g.get("concept") or ""
        related = set(int(x) for x in g.get("gold_pids") or [])
        core = set(core_pids(conn, con))
        cands = search_people(con, limit=60)
        pids = [p for p, _ in cands]
        hits = hits_of(con, pids)
        cites = {}
        for p in pids:
            r = conn.execute("SELECT cited_by_count FROM people WHERE pid=?", (p,)).fetchone()
            cites[p] = int(r[0]) if r else 0
        if mode == "cites":
            key = lambda p: (cites.get(p, 0),)
        elif mode == "hits":
            key = lambda p: (hits.get(p, 0), cites.get(p, 0))
        else:
            key = lambda p: (math.log1p(cites.get(p, 0)) + w * math.log1p(hits.get(p, 0)),)
        ranked = sorted(pids, key=key, reverse=True)
        top = ranked[:10]
        r5 += len(set(top[:5]) & related) / max(len(related), 1)
        r10 += len(set(top) & related) / max(len(related), 1)
        if core:
            core_n += 1
            x = len(set(top) & core) / len(core)
            rc += x
            if x > 0: core_cov += 1
    print(f"  [{mode} w={w}] R@5 {r5/n:.1%} R@10 {r10/n:.1%} | R_core {rc/max(core_n,1):.1%} 覆盖 {core_cov/max(core_n,1):.0%} · {time.time()-t0:.0f}s")

print("== 命中数 vs 被引 排序对照（55 概念）==")
run("cites")
run("hits")
for w in (0.5, 1.0, 2.0, 4.0):
    run("mix", w)
conn.close()
