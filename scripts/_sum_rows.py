import json, sys
rows=[json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
n=len(rows)
print("n=%d R@5 %.2f%% R@10 %.2f%% MRR %.3f 未命中 %s" % (
    n, 100*sum(r["r5"] for r in rows)/n, 100*sum(r["r10"] for r in rows)/n,
    sum(r["mrr"] for r in rows)/n, ",".join(r["id"] for r in rows if r["r10"]==0)))
