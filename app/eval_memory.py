"""记忆评测：五能力 × 10 条，共 50 条。

五个能力（对齐 LongMemEval 的范式）：
  extraction     单句抽取：说出"我做什么/我不做什么"能否落库
  multi_session  跨会话：上一次说的话，这一次还用得上
  temporal       时间推理：as-of 查询能区分"以前/现在"
  update         知识更新：转向后旧方向必须失效，且不能被推荐
  abstain        拒答：记忆里没有的偏好，不能凭空编

判定项（机械可判）：
  fact_any   期望的事实出现在"当前有效事实"里
  fact_none  这些值不允许出现在当前有效事实里（防过期事实复用）
  note       答案是否带记忆说明（"（已按你的长期记忆"）
  refused    是否拒答

用法：
  python -m app.eval_memory            # 表格 + 汇总
  python -m app.eval_memory --json     # JSON
  python -m app.eval_memory --limit 10
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "memory.jsonl"
NOTE_MARK = "（已按你的长期记忆"


def load_cases(path: Path | None = None) -> list[dict]:
    p = path or CASES_PATH
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _active_values(uid: str) -> list[str]:
    from app import user_memory
    try:
        return [(f.get("value") or "") for f in user_memory.facts(uid, only_active=True, limit=200)]
    except Exception:
        return []


def score_case(case: dict, result: dict, active_values: list[str],
               as_of_values: list[str] | None = None) -> dict:
    """纯函数：根据 case 的期望检查一次运行结果。"""
    text = (result or {}).get("answer") or ""
    values = active_values if case.get("ability") != "temporal" else (as_of_values or active_values)
    checks = {}
    exp = case.get("expect") or {}
    if exp.get("fact_any"):
        checks["fact_any_ok"] = any(any(w in v for v in values) for w in exp["fact_any"])
    if exp.get("fact_none"):
        checks["fact_none_ok"] = not any(any(w in v for v in values) for w in exp["fact_none"])
    if "note" in exp:
        checks["note_ok"] = (NOTE_MARK in text) == bool(exp["note"])
    if "refused" in exp:
        checks["refused_ok"] = bool((result or {}).get("refused")) == bool(exp["refused"])
    return {"id": case.get("id"), "ability": case.get("ability"),
            "checks": checks, "passed": all(checks.values()) if checks else True,
            "chars": len(text)}


def run(cases: list[dict], log=print) -> dict:
    from app import memory_extract, supervisor, user_memory

    rows = []
    for c in cases:
        uid = f"eval-{c['id']}"
        try:
            user_memory.clear(uid)
        except Exception:
            pass
        # 1) 用自然语言"说"给系统听（走抽取，而不是直接写库）
        for q in (c.get("setup_queries") or []):
            memory_extract.apply(uid, q, sid=f"{uid}-setup")
        # 2) 可选：直接按时间写事实（temporal 用）
        for f in (c.get("setup_facts") or []):
            user_memory.add_fact(uid, f["key"], f["value"], at=f.get("at"))
        # 3) 跑对话
        t0 = time.perf_counter()
        r = None
        for q in (c.get("turns") or [c["q"]]):
            r = supervisor.run(q, sid=f"{uid}-sid", log=lambda *_: None, uid=uid)
        ms = (time.perf_counter() - t0) * 1000
        active = _active_values(uid)
        as_of = None
        if c.get("as_of"):
            try:
                as_of = [(f.get("value") or "") for f in
                         user_memory.facts(uid, as_of=c["as_of"], limit=200)]
            except Exception:
                as_of = []
        row = score_case(c, r or {}, active, as_of)
        row["ms"] = round(ms, 1)
        # 记忆是否真的进入了检索（fills 非空），而不只是答案前加了说明
        row["memory_fills"] = list((r or {}).get("memory_fills") or [])
        rows.append(row)
        flag = "✓" if row["passed"] else "✗"
        bad = [k for k, v in row["checks"].items() if not v]
        log(f"  {flag} {row['id']:<12} {row['ability']:<13} {row['ms']:>7.1f}ms "
            + ("" if not bad else "失败: " + ",".join(bad)))

    n = len(rows) or 1
    by_ability: dict[str, dict] = {}
    for r in rows:
        a = by_ability.setdefault(r["ability"], {"n": 0, "passed": 0})
        a["n"] += 1
        a["passed"] += 1 if r["passed"] else 0
    for a in by_ability.values():
        a["pass_rate"] = round(a["passed"] / a["n"], 4)
    stale_cases = [r for r in rows if "fact_none_ok" in r["checks"]]
    ms = [r["ms"] for r in rows]
    summary = {
        "cases": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "by_ability": by_ability,
        "stale_rate": round(sum(1 for r in stale_cases if not r["checks"]["fact_none_ok"])
                            / max(len(stale_cases), 1), 4),
        "latency_ms_p95": round(sorted(ms)[max(0, int(n * 0.95) - 1)], 1) if ms else 0,
        "fills_rate": round(sum(1 for r in rows if r.get("memory_fills")) / n, 4),
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
          f" · 过期事实误用 {s['stale_rate']:.0%} · 记忆进检索 {s['fills_rate']:.0%}"
          f" · p95 {s['latency_ms_p95']}ms")
    for k, v in sorted(s["by_ability"].items()):
        print(f"    {k:<14} {v['pass_rate']:.0%}  ({v['passed']}/{v['n']})")
    if s["failed"]:
        print("  未通过：" + ", ".join(s["failed"]))


if __name__ == "__main__":
    main()
