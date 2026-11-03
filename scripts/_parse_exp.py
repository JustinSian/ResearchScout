import json, sys
d = json.load(sys.stdin)
print("R@5 %.1f%% R@10 %.1f%% MRR %.3f" % (d["recall@5"]*100, d["recall@10"]*100, d["mrr"]))
