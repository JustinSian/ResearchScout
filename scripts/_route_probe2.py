from app.agent import tool_find_expert
tests = [
 "graph neural network", "perovskite solar cell", "CRISPR screening",
 "single cell RNA sequencing", "mRNA vaccine", "transformer efficiency",
 "quantum error correction", "protein structure prediction",
 "recommend experts on graph neural network",
 "who works on perovskite solar cells",
]
for q in tests:
    r = tool_find_expert(q, limit=5)
    print(f"{q[:44]:<46} mode={str(r.get('mode')):<6} n={r.get('count')} ce_max={r.get('ce_max')}")
