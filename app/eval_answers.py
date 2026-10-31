"""答案级评测：不只看"调了什么工具"，还看"回答里有没有该有的内容"。

为什么要它：`app/agent_eval.py` 只校验工具选择/是否拒答/是否带引用，
一个把名字答错、把数字说反的回答同样能通过。这个评测补的是**内容约束**。

判定项（全部可机械判定，不做主观打分）：
  tools_ok    —— 首个工具是否等于期望
  refuse_ok   —— 该拒的拒了、不该拒的没拒
  cited_ok    —— 非拒答回答必须带引用
  must_any_ok —— 至少命中一个期望片段（片段是"同义写法"，不锁死措辞）
  must_all_ok —— 全部期望片段都要出现
  must_not_ok —— 不能出现的片段（例如"没有找到"）

支持多轮：case.turns 依次发送（共用 sid），只评最后一轮。

用法：
  python -m app.eval_answers            # 表格 + 汇总
  python -m app.eval_answers --json     # 只输出 JSON（便于入库/对比）
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from app.supervisor import run as supervise

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "answers.jsonl"


def load_cases(path: Path | None = None) -> list[dict]:
    p = path or CASES_PATH
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def check_answer(case: dict, result: dict) -> dict:
    """纯函数：根据 case 约束检查一个 agent 结果。"""
    text = result.get("answer") or ""
    refused = bool(result.get("refused"))
    tools = [p.get("tool") for p in (result.get("plan") or [])]
    cites = result.get("citations") or []

    checks = {}
    if "tools" in case:
        checks["tools_ok"] = bool(tools) and tools[0] in case["tools"]
    if "refuse" in case:
        checks["refuse_ok"] = refused == bool(case["refuse"])
    if "kb_refuse" in case:
        # 库内是否拒答；为 True 时允许随后走网络兜底（fallback 是特性，不是失败）
        checks["kb_refuse_ok"] = bool(result.get("kb_refused")) == bool(case["kb_refuse"])
    checks["cited_ok"] = bool(cites) if not refused else True
    if case.get("must_any"):
        checks["must_any_ok"] = any(s in text for s in case["must_any"])
    if case.get("must_all"):
        checks["must_all_ok"] = all(s in text for s in case["must_all"])
    if case.get("must_not"):
        checks["must_not_ok"] = all(s not in text for s in case["must_not"])

    return {"id": case.get("id") or case.get("q"), "checks": checks,
            "passed": all(checks.values()), "tools": tools, "refused": refused,
            "citations": len(cites), "chars": len(text)}


def run(cases: list[dict], log=print) -> dict:
    rows = []
    for c in cases:
        sid = c.get("sid", "")
        turns = c.get("turns") or [c["q"]]
        t0 = time.perf_counter()
        r = None
        for q in turns:
            r = supervise(q, sid=sid, log=lambda *_: None)   # 与 /chat 同一条链路
        ms = (time.perf_counter() - t0) * 1000
        row = check_answer(c, r or {})
        row["ms"] = round(ms, 1)
        rows.append(row)
        flag = "✓" if row["passed"] else "✗"
        bad = [k for k, v in row["checks"].items() if not v]
        log(f"  {flag} {row['id']:<18} {row['ms']:>7.1f}ms  "
            + ("" if not bad else "失败项: " + ",".join(bad)))

    n = len(rows) or 1
    names = sorted({k for r in rows for k in r["checks"]})
    summary = {
        "cases": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "by_check": {k: round(sum(r["checks"].get(k, True) for r in rows) / n, 4)
                     for k in names},
        "latency_ms_p50": round(statistics.median([r["ms"] for r in rows]), 1) if rows else 0,
        "latency_ms_p95": round(sorted(r["ms"] for r in rows)[max(0, int(n * 0.95) - 1)], 1)
        if rows else 0,
        "failures": [r["id"] for r in rows if not r["passed"]],
    }
    return {"summary": summary, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--file", default="")
    a = ap.parse_args()
    cases = load_cases(Path(a.file) if a.file else None)
    if a.limit:
        cases = cases[:a.limit]
    res = run(cases, log=(lambda *_: None) if a.json else print)
    if a.json:
        print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
        return
    s = res["summary"]
    print()
    print(f"  通过率 {s['pass_rate']:.1%}（{int(s['pass_rate']*s['cases'])}/{s['cases']}）"
          f"  延迟 p50 {s['latency_ms_p50']}ms / p95 {s['latency_ms_p95']}ms")
    print("  分项：" + "  ".join(f"{k}={v:.0%}" for k, v in s["by_check"].items()))
    if s["failures"]:
        print("  未通过：" + ", ".join(s["failures"]))


if __name__ == "__main__":
    main()
