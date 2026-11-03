import os
from app import eval_experts
from app.eval_core import core_pids
from app.db import connect
from app.agent import tool_find_expert
gold=eval_experts.load_gold()
print(f"{'mix':>7} | {'R@5':>6} {'R@10':>6} {'MRR':>6} | {'R_core':>7} {'cover':>6}")
for mix in ("off","tail1","tail2","tail3","tail4"):
    os.environ["ST_CORE_MIX"]=mix; os.environ["ST_POS_WEIGHT"]="0"
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
    print(f"{mix:>7} | {s['recall@5']:>6.1%} {s['recall@10']:>6.1%} {s['mrr']:>6.3f} | {sum(rec)/n:>7.1%} {cov/n:>6.0%}")
