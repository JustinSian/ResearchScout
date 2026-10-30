"""路由评测：Supervisor 的调度决策 vs 旧版单轮规划器。

只评"路由对不对"，不执行工具——执行代价高且不影响路由指标。
若要端到端指标，另见 `--execute`。

用法：
  python -m app.eval_routing            # 只评路由
  python -m app.eval_routing --execute   # 顺带跑 3 个代表性问题看答案
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db import connect

EVAL = Path(__file__).resolve().parents[1] / "eval" / "routing.json"

# 旧版：单轮规划器的工具 → 归为"kb"或"task"
_OLD_KB = {"profile", "research_area", "relation", "collaborators",
           "find_expert", "works", "compare", "search"}


def old_route(query: str) -> str:
    from app.agent import plan_node
    tool = (plan_node({"query": query})["plan"] or [{}])[0].get("tool", "find_expert")
    return "task" if tool == "task" else ("kb" if tool in _OLD_KB else "kb")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--file", default="", help="评测集路径（默认 eval/routing.json）")
    a = ap.parse_args()
    path = Path(a.file) if a.file else EVAL
    if not path.exists():
        raise SystemExit(f"缺少评测集：{path}")

    from app.supervisor import decide

    cases = json.loads(path.read_text(encoding="utf-8"))
    old_ok = new_ok = 0
    rows = []
    for c in cases:
        q, exp = c["q"], c["expect"]
        o = old_route(q)
        d = decide(q)
        n = d["route"]
        old_ok += (o == exp)
        new_ok += (n == exp)
        rows.append((q, exp, o, n, d["reason"]))

    print(f"{'问题':<30}{'期望':<14}{'旧版':<10}{'新版':<14}{'理由'}")
    for q, exp, o, n, why in rows:
        m1 = "✓" if o == exp else "✗"
        m2 = "✓" if n == exp else "✗"
        print(f"{q[:28]:<30}{exp:<14}{o+m1:<10}{n+m2:<14}{why[:22]}")

    n = len(cases)
    print(f"\n  旧版路由准确率: {old_ok}/{n} = {old_ok/n:.0%}")
    print(f"  新版路由准确率: {new_ok}/{n} = {new_ok/n:.0%}")

    if a.execute:
        from app.supervisor import run
        print("\n  == 端到端抽查 ==")
        for q in ("钟南山最近有什么新闻", "今天北京天气怎么样", "介绍一下钟南山"):
            r = run(q, log=lambda s: None)
            print(f"  Q: {q}")
            print(f"     route={r['route']} kb_refused={r['kb_refused']} web={r['web_used']} "
                  f"引用={len(r['citations'])}")
            print(f"     A: {r['answer'][:120].replace(chr(10),' ')}")


if __name__ == "__main__":
    main()
