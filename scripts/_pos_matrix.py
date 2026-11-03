import os, json
from app import eval_experts, eval_core
from app.eval_core import core_pids
from app.db import connect
from app.agent import tool_find_expert

gold = eval_experts.load_gold()
print(f"{'weight':>7} | {'R@5':>6} {'R@10':>6} {'MRR':>6} | {'R_core@10':>9} {'cover':>6}")
for w in ("0", "0.6", "1.2", "2.0", "3.0"):
    os.environ["ST_POS_WEIGHT"] = w
    s = eval_experts.run(gold, log=lambda *_: None)["summary"]
    conn = connect()
    recalls = []
    covered = 0
    try:
        for g in gold:
            con = g.get("concept") or ""
            core = core_pids(conn, con)
            if not core:
                continue
            r = tool_find_expert(con, limit=10)
            hit = [int(it["pid"]) for it in (r.get("items") or []) if it.get("pid")]
            rec = len(set(hit[:10]) & set(core)) / len(core)
            recalls.append(rec)
            covered += 1 if rec > 0 else 0
    finally:
        conn.close()
    n = len(recalls) or 1
    print(f"{w:>7} | {s['recall@5']:>6.1%} {s['recall@10']:>6.1%} {s['mrr']:>6.3f} | "
          f"{sum(recalls)/n:>9.1%} {covered/n:>6.0%}")
