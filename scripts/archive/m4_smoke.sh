#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python
echo "== Agent 冒烟（5 类问题） =="
"$PY" - <<'PY'
from app.agent import answer
qs = [
 "找研究 graph neural network 的专家",
 "介绍一下 Yoshua Bengio",
 "谁和 Yoshua Bengio 合作过",
 "找 transformer 相关的论文",
 "对比 Yoshua Bengio 和 Geoffrey Hinton",
 "找研究 quantum gravity phenology 的专家",
]
for q in qs:
    r = answer(q)
    plan = [p["tool"] for p in r["plan"]]
    print(f"\nQ: {q}")
    print(f"  tool={plan} refused={r['refused']} citations={len(r['citations'])}")
    print("  A:", r["answer"][:180].replace("\n", " | "))
PY
