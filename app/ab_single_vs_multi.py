"""A/B：supervisor 多智能体编排 vs 单 agent 一次性规划。

为什么要这个对照：项目里"多智能体"一直只有"我做了"的叙述，没有"它更好"的证据。
对照组就是最朴素的实现——**一个 agent、一次性规划、所有工具都能用**（`agent.answer`），
多智能体组是 **supervisor 先路由，再交给对应子 agent**（`supervisor.run`）。

**成本口径必须说清**：这个项目的答案链路里没有 LLM（规则 + 检索 + 模板），
所以"token 成本"两边都是 0，用它对比会得出"多智能体不花钱"这种假结论。
这里改用三个可测的量：
  tool_calls  —— 实际执行的工具调用次数
  latency_ms  —— 端到端耗时
  web_hit     —— 是否触发了网络兜底（外部依赖 = 真实成本）

质量判定复用 `app/eval_answers.check_answer` 的约束（同一套 gold set）。

用法：
  python -m app.ab_single_vs_multi            # 表格
  python -m app.ab_single_vs_multi --json     # JSON
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from app.agent import answer as single_answer
from app.eval_answers import CASES_PATH, check_answer, load_cases
from app.supervisor import run as multi_answer

ROOT = Path(__file__).resolve().parents[1]


def _run_one(case: dict, path: str) -> dict:
    sid = f"ab-{path}-{case.get('id') or abs(hash(case.get('q', ''))) % 10**6}"
    turns = case.get("turns") or [case["q"]]
    fn = single_answer if path == "single" else multi_answer
    t0 = time.perf_counter()
    r = None
    for q in turns:
        if path == "single":
            r = fn(q, sid=sid)
        else:
            r = fn(q, sid=sid, log=lambda *_: None)
    ms = (time.perf_counter() - t0) * 1000
    plan = (r or {}).get("plan") or []
    return {
        "quality": check_answer(case, r or {}),
        "ms": round(ms, 1),
        "tool_calls": len(plan),
        "web_hit": bool((r or {}).get("fallback")) or bool((r or {}).get("kb_refused"))
        and not (r or {}).get("refused"),
        "refused": bool((r or {}).get("refused")),
        "tools": [p.get("tool") for p in plan],
        "route": (r or {}).get("route"),
        "citations": len((r or {}).get("citations") or []),
    }


def _agg(rows: list[dict]) -> dict:
    n = len(rows) or 1
    ms = [r["ms"] for r in rows]
    return {
        "n": len(rows),
        "pass_rate": round(sum(r["quality"]["passed"] for r in rows) / n, 4),
        "tool_calls_total": sum(r["tool_calls"] for r in rows),
        "tool_calls_mean": round(sum(r["tool_calls"] for r in rows) / n, 2),
        "web_hit_rate": round(sum(1 for r in rows if r["web_hit"]) / n, 4),
        "refuse_rate": round(sum(1 for r in rows if r["refused"]) / n, 4),
        "latency_p50": round(statistics.median(ms), 1) if ms else 0,
        "latency_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "failed_ids": [r["quality"]["id"] for r in rows if not r["quality"]["passed"]],
    }


def run(cases: list[dict], log=print) -> dict:
    out: dict[str, dict] = {}
    for path in ("single", "multi"):
        rows = []
        for c in cases:
            rows.append(_run_one(c, path))
        out[path] = {"rows": rows, "summary": _agg(rows)}
        log(f"  {path:<7} 通过率 {out[path]['summary']['pass_rate']:.0%} "
            f"工具调用 {out[path]['summary']['tool_calls_total']} "
            f"p50 {out[path]['summary']['latency_p50']}ms "
            f"联网 {out[path]['summary']['web_hit_rate']:.0%}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cases = load_cases(CASES_PATH)
    if a.limit:
        cases = cases[:a.limit]
    res = run(cases, log=(lambda *_: None) if a.json else print)
    if a.json:
        print(json.dumps({k: v["summary"] for k, v in res.items()}, ensure_ascii=False, indent=2))
        return
    s, m = res["single"]["summary"], res["multi"]["summary"]
    print()
    print(f"  {'指标':<14}{'单 agent':>12}{'多智能体':>12}{'差值':>12}")
    rows = [
        ("通过率", f"{s['pass_rate']:.0%}", f"{m['pass_rate']:.0%}",
         f"{(m['pass_rate']-s['pass_rate'])*100:+.0f}pt"),
        ("工具调用总数", s["tool_calls_total"], m["tool_calls_total"],
         f"{m['tool_calls_total']-s['tool_calls_total']:+d}"),
        ("联网兜底率", f"{s['web_hit_rate']:.0%}", f"{m['web_hit_rate']:.0%}",
         f"{(m['web_hit_rate']-s['web_hit_rate'])*100:+.0f}pt"),
        ("拒答率", f"{s['refuse_rate']:.0%}", f"{m['refuse_rate']:.0%}",
         f"{(m['refuse_rate']-s['refuse_rate'])*100:+.0f}pt"),
        ("延迟 p50", f"{s['latency_p50']}ms", f"{m['latency_p50']}ms",
         f"{m['latency_p50']-s['latency_p50']:+.0f}ms"),
        ("延迟 p95", f"{s['latency_p95']}ms", f"{m['latency_p95']}ms",
         f"{m['latency_p95']-s['latency_p95']:+.0f}ms"),
    ]
    for r in rows:
        print(f"  {r[0]:<14}{str(r[1]):>12}{str(r[2]):>12}{str(r[3]):>12}")
    if s["failed_ids"]:
        print("  单 agent 未通过：" + ", ".join(s["failed_ids"]))
    if m["failed_ids"]:
        print("  多智能体未通过：" + ", ".join(m["failed_ids"]))


if __name__ == "__main__":
    main()
