"""回归基线：一条命令判断"这次改动有没有把指标搞坏"。

做法：跑固定的两组评测（24 题 Agent + 50 题检索），与 `eval/baseline.json` 对比。
任何一项下降超过容差 → 退出码 1，可直接挂 CI/部署前检查。

  python -m app.regression            # 对比基线
  python -m app.regression --save     # 把当前结果存成基线
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

BASELINE = Path("eval/baseline.json")
TOL = 0.02          # 允许 2 个百分点的抖动


def _agent_metrics() -> dict:
    from app.agent import answer
    from app.agent_eval import CASES
    tool_ok = refuse_ok = cited = 0
    n = len(CASES)
    for q, expect_tool, should_refuse in CASES:
        r = answer(q)
        tools = [p["tool"] for p in r["plan"]]
        tool_ok += bool(tools) and tools[0] == expect_tool
        refused = bool(r["refused"])
        refuse_ok += refused == should_refuse
        if not refused:
            cited += bool(r["citations"])
    return {"tool_acc": round(tool_ok / n, 4), "refuse_acc": round(refuse_ok / n, 4),
            "cite_rate": round(cited / max(1, sum(1 for _q, _t, sr in CASES if not sr)), 4)}


def _retrieval_metrics() -> dict:
    from app.evaluate import CONFIGS, metrics, run_config
    gold = json.load(open("eval/gold_alt.json", encoding="utf-8"))
    out = {}
    for name, cfg in CONFIGS:
        if name not in ("bm25", "dense", "rrf_k60"):
            continue
        vals = []
        for g in gold:
            ranked = run_config(cfg, g["query"], g["kind"])
            vals.append(metrics(ranked, set(g["expected"])))
        out[name] = round(sum(v[2] for v in vals) / max(len(vals), 1), 4)
    return {f"ndcg_{k}": v for k, v in out.items()}


def main() -> int:
    cur = {**_agent_metrics(), **_retrieval_metrics()}
    print("当前指标:", json.dumps(cur, ensure_ascii=False))
    if "--save" in sys.argv or not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入基线 {BASELINE}")
        return 0
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    worse = []
    for k, v in base.items():
        if k not in cur:
            continue
        if cur[k] + TOL < v:
            worse.append((k, v, cur[k]))
    for k, v, c in worse:
        print(f"  ✗ {k}: {v:.3f} → {c:.3f}（退化 {v - c:.3f}）")
    print(f"回归检查：{'通过' if not worse else f'{len(worse)} 项退化'}")
    return 1 if worse else 0


if __name__ == "__main__":
    raise SystemExit(main())
