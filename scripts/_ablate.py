import os
from app import eval_experts
from app.eval_core import core_pids
from app.db import connect
from app.agent import tool_find_expert
gold=eval_experts.load_gold()
def measure():
    s=eval_experts.run(gold, log=lambda *_: None)["summary"]
    conn=connect(); rec=[]; cov=0
    try:
        for g in gold:
            con=g.get("concept") or ""
            core=core_pids(conn, con)
            if not core: continue
            r=tool_find_expert(con, limit=10)
            hit=[int(it["pid"]) for it in (r.get("items") or []) if it.get("pid")]
            x=len(set(hit[:10])&set(core))/len(core); rec.append(x); cov+=1 if x>0 else 0
    finally: conn.close()
    n=len(rec) or 1
    return s["recall@5"], s["recall@10"], s["mrr"], sum(rec)/n, cov/n
print(f"{'config':<34} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'R_core':>7} {'cov':>5}")
for name, env in [
  ("baseline tail1", {"ST_CORE_MIN_HITS":"1","ST_TOPIC_POOL":"100"}),
  ("min_hits=2",     {"ST_CORE_MIN_HITS":"2","ST_TOPIC_POOL":"100"}),
  ("min_hits=3",     {"ST_CORE_MIN_HITS":"3","ST_TOPIC_POOL":"100"}),
  ("pool=200",       {"ST_CORE_MIN_HITS":"1","ST_TOPIC_POOL":"200"}),
  ("min_hits=2+pool200", {"ST_CORE_MIN_HITS":"2","ST_TOPIC_POOL":"200"}),
]:
    for k,v in env.items(): os.environ[k]=v
    r5,r10,mrr,rc,cov=measure()
    print(f"{name:<34} {r5:>6.1%} {r10:>6.1%} {mrr:>6.3f} {rc:>7.1%} {cov:>5.0%}")
