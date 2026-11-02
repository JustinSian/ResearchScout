"""A/B：规则路径 vs LLM 工具循环（ReAct）。

对照组是 `supervisor.run`（规则路由 + 单次工具调用 + 模板作答），
实验组是 `loop_llm.run`（模型自己决定调几次工具、何时收尾）。

指标：
  pass_rate   答案级约束通过率（同一套 check_answer）
  tool_match  首次工具是否符合期望
  steps       平均工具调用次数（循环的核心成本）
  cost_usd    token 成本
  latency     p50 / p95

用法：
  python -m app.ab_loop
  python -m app.ab_loop --limit 10
"""
from __future__ import annotations

import argparse
import os
import statistics
import time

from app.eval_answers import CASES_PATH, check_answer, load_cases


def _run_rules(cases: list[dict]) -> list[dict]:
    from app.supervisor import run as supervise
    rows = []
    for c in cases:
        sid = f"abloop-rules-{c.get('id')}"
        turns = c.get("turns") or [c["q"]]
        t0 = time.perf_counter()
        r = None
        for q in turns:
            r = supervise(q, sid=sid, log=lambda *_: None)
        ms = (time.perf_counter() - t0) * 1000
        chk = check_answer(c, r or {})
        tools = [p.get("tool") for p in ((r or {}).get("plan") or [])]
        rows.append({"id": chk["id"], "passed": chk["passed"], "tools": tools, "ms": ms,
                     "cost_usd": 0.0, "steps": len(tools),
                     "tokens": 0})
    return rows


def _run_loop(cases: list[dict], budget: float) -> list[dict]:
    from app import loop_llm
    rows = []
    for c in cases:
        sid = f"abloop-loop-{c.get('id')}"
        turns = c.get("turns") or [c["q"]]
        t0 = time.perf_counter()
        r, steps, cost, tokens = None, 0, 0.0, 0
        for q in turns:
            r = loop_llm.run(q, sid=sid, budget_usd=budget)
            if r:
                steps += len(r.get("steps") or [])
                cost += float(r.get("cost_usd") or 0.0)
                u = r.get("usage") or {}
                tokens += int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
        ms = (time.perf_counter() - t0) * 1000
        if not r:
            rows.append({"id": c.get("id"), "passed": False, "tools": [], "ms": ms,
                         "cost_usd": cost, "steps": steps, "tokens": tokens,
                         "error": "loop_failed"})
            continue
        mapped = {"answer": r.get("answer", ""), "citations": r.get("citations") or [],
                  "refused": bool(r.get("refused")),
                  "plan": [{"tool": s.get("tool"), "args": s.get("args") or {}}
                           for s in (r.get("steps") or [])]}
        chk = check_answer(c, mapped)
        rows.append({"id": chk["id"], "passed": chk["passed"],
                     "tools": [s.get("tool") for s in (r.get("steps") or [])],
                     "ms": ms, "cost_usd": cost, "steps": steps, "tokens": tokens})
    return rows


def _summary(name: str, rows: list[dict], cases: list[dict]) -> dict:
    n = len(rows) or 1
    ms = [r["ms"] for r in rows]
    expect = {c.get("id"): (c.get("tools") or []) for c in cases}
    matched = 0
    for r in rows:
        exp = expect.get(r["id"]) or []
        if not exp or (r["tools"] and r["tools"][0] in exp):
            matched += 1
    return {
        "mode": name,
        "n": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "tool_match": round(matched / n, 4),
        "steps_mean": round(sum(r["steps"] for r in rows) / n, 2),
        "latency_p50": round(statistics.median(ms), 1) if ms else 0,
        "latency_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "tokens": sum(r["tokens"] for r in rows),
        "failed": [r["id"] for r in rows if not r["passed"]],
        "rows": rows,
    }


def run(modes: list[str], cases: list[dict], budget: float = 0.02, log=print) -> dict:
    from app import llm
    out = {}
    for m in modes:
        if m == "loop" and not llm.configured():
            log("  loop   跳过（未配置 DEEPSEEK_API_KEY）")
            out[m] = {"mode": m, "skipped": True}
            continue
        rows = _run_rules(cases) if m == "rules" else _run_loop(cases, budget)
        s = _summary(m, rows, cases)
        out[m] = s
        log(f"  {m:<6} 通过 {s['pass_rate']:.0%} · 工具匹配 {s['tool_match']:.0%} · "
            f"步数 {s['steps_mean']} · p50 {s['latency_p50']}ms · ${s['cost_usd']:.5f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="rules,loop")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=float, default=0.02)
    a = ap.parse_args()
    cases = load_cases(CASES_PATH)
    if a.limit:
        cases = cases[: a.limit]
    os.environ.setdefault("ST_PLANNER", "rules")     # 规则组走纯规则，避免混入 LLM 规划
    res = run([m.strip() for m in a.modes.split(",") if m.strip()], cases, a.budget)
    keys = ["pass_rate", "tool_match", "steps_mean", "latency_p50", "latency_p95",
            "cost_usd", "tokens"]
    print()
    print(f"  {'指标':<12}" + "".join(f"{m:>14}" for m in res))
    for k in keys:
        line = ""
        for m in res:
            v = res[m].get(k)
            line += f"{'-':>14}" if v is None else (
                f"{v:>14.1%}" if k.endswith("rate") or k == "tool_match" else f"{v:>14}")
        print(f"  {k:<12}{line}")
    for m, s in res.items():
        if s.get("failed"):
            print(f"  {m} 未通过: " + ", ".join(s["failed"]))


if __name__ == "__main__":
    main()
