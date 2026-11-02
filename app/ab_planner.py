"""规划器 A/B：规则路由 vs LLM 路由（同一套 gold set，同一套判定）。

这是 m45 的延伸：m45 比的是"单 agent vs supervisor"，这一轮比的是
**supervisor 内部谁来做意图解析**——规则还是模型。

指标：
  pass_rate     答案级约束通过率（复用 eval_answers.check_answer）
  tool_match    首个工具是否等于期望
  latency       p50 / p95
  tokens/cost   仅 LLM 组有（规则组为 0）

用法：
  python -m app.ab_planner                 # 两组都跑（LLM 未配置时自动跳过）
  python -m app.ab_planner --modes rules,llm
"""
from __future__ import annotations

import argparse
import os
import statistics
import time

from app.agent import _LLM_PLAN_CACHE
from app.eval_answers import CASES_PATH, check_answer, load_cases
from app.supervisor import run as supervise


def _run_mode(mode: str, cases: list[dict], log=print) -> dict:
    os.environ["ST_PLANNER"] = mode
    _LLM_PLAN_CACHE.clear()
    rows = []
    for c in cases:
        sid = f"abplan-{mode}-{c.get('id')}"
        turns = c.get("turns") or [c["q"]]
        t0 = time.perf_counter()
        r = None
        for q in turns:
            r = supervise(q, sid=sid, log=lambda *_: None)
        ms = (time.perf_counter() - t0) * 1000
        plan = (r or {}).get("plan") or []
        tools = [p.get("tool") for p in plan]
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        cost = 0.0
        planners = []
        for p in plan:
            u = p.get("usage") or {}
            usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
            cost += float(p.get("cost_usd") or 0.0)
            planners.append(p.get("planner") or "rules")
        q = check_answer(c, r or {})
        expect = c.get("tools") or []
        rows.append({"id": q["id"], "passed": q["passed"], "tools": tools,
                     "tool_match": (not expect) or (bool(tools) and tools[0] in expect),
                     "ms": round(ms, 1), **usage, "cost_usd": cost,
                     "planners": planners})
    n = len(rows) or 1
    ms = [r["ms"] for r in rows]
    s = {
        "mode": mode,
        "n": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "tool_match": round(sum(r["tool_match"] for r in rows) / n, 4),
        "latency_p50": round(statistics.median(ms), 1) if ms else 0,
        "latency_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "failed": [r["id"] for r in rows if not r["passed"]],
        "rows": rows,
    }
    log(f"  {mode:<6} 通过 {s['pass_rate']:.0%} · 工具匹配 {s['tool_match']:.0%} · "
        f"p50 {s['latency_p50']}ms · tokens {s['prompt_tokens']}+{s['completion_tokens']}")
    return s


def run(modes: list[str], cases: list[dict] | None = None, log=print) -> dict:
    from app import llm
    cases = cases or load_cases(CASES_PATH)
    out = {}
    for mode in modes:
        if mode == "llm" and not llm.configured():
            log("  llm    跳过（DEEPSEEK_API_KEY 未配置）")
            out[mode] = {"mode": mode, "skipped": True}
            continue
        out[mode] = _run_mode(mode, cases, log=log)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="rules,llm")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    modes = [m.strip() for m in a.modes.split(",") if m.strip()]
    cases = load_cases(CASES_PATH)
    if a.limit:
        cases = cases[: a.limit]
    res = run(modes, cases)
    keys = ["pass_rate", "tool_match", "latency_p50", "latency_p95", "cost_usd"]
    print()
    print(f"  {'指标':<12}" + "".join(f"{m:>14}" for m in res))
    for k in keys:
        row = ""
        for m in res:
            v = res[m].get(k)
            row += f"{'-':>14}" if v is None else (f"{v:>14.1%}" if k.endswith("rate") or k == "tool_match"
                                                  else f"{v:>14}")
        print(f"  {k:<12}{row}")


if __name__ == "__main__":
    main()
