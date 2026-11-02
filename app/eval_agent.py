"""Agent 级评测：多轮对话 + 记忆依赖 + 工具选择 + 成本。

和 `eval_answers.py` 的区别：
  eval_answers  单轮、针对旧确定性链路
  本文件        走**新的 LLM Agent**（agent_llm.chat），多轮、跨会话记忆、追问

判定：
  must_any     回答里必须出现的片段（可多个同义写法）
  must_not     禁止出现（例如编造的数字）
  refused      是否应该拒答
  tools_any    期望调用过的工具（任一即可）
  memory       是否应该用上长期记忆（回答带"（已按你的长期记忆"或 remember 工具被调用）

汇总：通过率 / 工具命中 / 记忆使用率 / 平均轮数 / 成本 / p50 p95
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "agent_cases.jsonl"
NOTE_MARK = "（已按你的长期记忆"


def load_cases(path: Path | None = None) -> list[dict]:
    p = path or CASES_PATH
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def score_case(case: dict, results: list[dict]) -> dict:
    """纯函数：多轮结果里只看最后一轮，但工具是全部轮次的并集。"""
    last = results[-1] if results else {}
    text = (last.get("answer") or "")
    tools = [s.get("tool") for r in results for s in (r.get("steps") or [])]
    checks = {}
    exp = case.get("expect") or {}
    if exp.get("must_any"):
        checks["must_any_ok"] = any(w in text for w in exp["must_any"])
    if exp.get("must_not"):
        checks["must_not_ok"] = all(w not in text for w in exp["must_not"])
    if "refused" in exp:
        checks["refused_ok"] = bool(last.get("refused")) == bool(exp["refused"])
    if exp.get("tools_any"):
        checks["tools_ok"] = any(t in exp["tools_any"] for t in tools)
    if exp.get("memory"):
        remember_called = "remember" in tools
        noted = (NOTE_MARK in text) or bool(last.get("memory"))
        checks["memory_ok"] = remember_called or noted
    passed = all(checks.values()) if checks else True
    return {"id": case.get("id"), "checks": checks, "passed": passed,
            "tools": tools, "chars": len(text),
            "cost": round(sum(float(r.get("cost_usd") or 0) for r in results), 6)}


def run(cases: list[dict], log=print) -> dict:
    from app import agent_llm, session_memory, user_memory

    rows = []
    for c in cases:
        uid = f"eval-{c['id']}"
        sid = f"eval-{c['id']}-sid"
        try:
            user_memory.clear(uid)
            session_memory.reset(sid)
        except Exception:
            pass
        for q in (c.get("setup_queries") or []):
            from app import memory_extract
            memory_extract.apply(uid, q, sid=sid)
        t0 = time.perf_counter()
        results = []
        for q in (c.get("turns") or [c["q"]]):
            r = agent_llm.chat(q, uid=uid, sid=sid)
            results.append(r)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        row = score_case(c, results)
        row["ms"] = ms
        row["rounds"] = len(results)
        rows.append(row)
        flag = "✓" if row["passed"] else "✗"
        bad = [k for k, v in row["checks"].items() if not v]
        log(f"  {flag} {row['id']:<14} {row['rounds']} 轮 {row['ms']:>8.1f}ms "
            f"${row['cost']:.4f} " + ("" if not bad else "失败: " + ",".join(bad)))

    n = len(rows) or 1
    ms = [r["ms"] for r in rows]
    summary = {
        "cases": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "tool_hit": round(sum(1 for r in rows if r["checks"].get("tools_ok", True)) / n, 4),
        "memory_use": round(sum(1 for r in rows if r["checks"].get("memory_ok", False)) / n, 4),
        "rounds_mean": round(sum(r["rounds"] for r in rows) / n, 2),
        "cost_usd": round(sum(r["cost"] for r in rows), 6),
        "latency_p50": round(statistics.median(ms), 1) if ms else 0,
        "latency_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "failed": [r["id"] for r in rows if not r["passed"]],
    }
    return {"summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cases = load_cases()
    if a.limit:
        cases = cases[:a.limit]
    res = run(cases, log=(lambda *_: None) if a.json else print)
    s = res["summary"]
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print()
    print(f"  通过率 {s['pass_rate']:.0%}（{int(s['pass_rate']*s['cases'])}/{s['cases']}）"
          f" · 工具命中 {s['tool_hit']:.0%} · 记忆使用 {s['memory_use']:.0%}")
    print(f"  平均轮数 {s['rounds_mean']} · 成本 ${s['cost_usd']:.4f}"
          f" · p50 {s['latency_p50']}ms / p95 {s['latency_p95']}ms")
    if s["failed"]:
        print("  未通过：" + ", ".join(s["failed"]))


if __name__ == "__main__":
    main()
