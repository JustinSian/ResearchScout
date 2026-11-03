"""Agent 级的自我进化：从**失败轨迹**里改策略提示词，用双闸门决定是否采纳。

和 `app/rsi.py` 的分工：
  rsi.py        进化**检索词**（synonyms），评测走规则链路，便宜、快
  本模块         进化**Agent 策略**（plan_prompt），评测走真实 LLM，慢、贵，但影响面更大

流程：
  1. 用当前策略在 dev 集上跑一遍，收集失败用例（问题、调了哪些工具、答案片段、失败项）
  2. 把失败轨迹交给模型，让它写一段"策略补丁"（≤200 字）
  3. 候选在 dev 上重跑；dev 有提升才允许看 held-out
  4. held-out 不退步才晋级（写快照 + rsi_log），否则拒绝并记录原因

成本控制：
  --generations / --budget-usd / 每代最多 2 个候选 / dev 8 条
  实测一代约 0.05–0.10 美元，8–10 分钟。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app import agent_llm, llm, rsi
from app.eval_agent import score_case

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "eval" / "agent_dev.jsonl"
HELDOUT = ROOT / "eval" / "agent_heldout.jsonl"

MAX_PROMPT_CHARS = 240

PROPOSE_SYSTEM = (
    "你是 Agent 的策略优化器。用户会给出**失败用例**（问题、模型调了哪些工具、答案片段、失败项）"
    "和**当前策略**。请写一段不超过 200 字的补充策略，帮助 Agent 在这些失败上做对，"
    "同时不要破坏已通过的行为。\n"
    "要求：只输出 JSON，形如 {\"plan_prompt\": \"...\"}；\n"
    "内容必须是可执行的行动规则（例如'涉及人名时先调 profile；证据为空时直接说没有找到'），"
    "不要写泛泛的鼓励语，不要重复已有策略。"
)


def load_cases(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------- 评估 ----------------

def evaluate_agent(policy: dict, cases: list[dict], log=print) -> dict:
    """在给定策略下跑 agent_llm，返回通过率与逐条明细。"""
    prev_policy, prev_gen = rsi.active_policy(), rsi._ACTIVE_GEN
    rsi._ACTIVE, rsi._ACTIVE_GEN = policy, prev_gen
    t0 = time.perf_counter()
    rows = []
    from app import memory_extract, session_memory, user_memory
    try:
        for c in cases:
            uid = f"rsiagent-{c['id']}"
            sid = f"rsiagent-{c['id']}-sid"
            # 和 eval_agent 一样：先清空、再按 setup 写入长期记忆，否则记忆类用例必失败
            try:
                user_memory.clear(uid)
                session_memory.reset(sid)
            except Exception:
                pass
            for sq in (c.get("setup_queries") or []):
                try:
                    memory_extract.apply(uid, sq, sid=sid)
                except Exception:
                    pass
            results = []
            for q in (c.get("turns") or [c["q"]]):
                results.append(agent_llm.chat(q, uid=uid, sid=sid))
            sc = score_case(c, results)
            last = results[-1] if results else {}
            sc["q"] = c.get("q") or (c.get("turns") or [""])[0]
            sc["answer"] = (last.get("answer") or "")[:200]
            sc["failed_checks"] = [k for k, v in sc["checks"].items() if not v]
            sc["steps"] = sum(len(r.get("steps") or []) for r in results)
            rows.append(sc)
    finally:
        rsi._ACTIVE, rsi._ACTIVE_GEN = prev_policy, prev_gen
    n = len(rows) or 1
    passed = sum(1 for r in rows if r["passed"])
    return {"n": len(rows), "passed": passed, "pass_rate": round(passed / n, 4),
            "steps_mean": round(sum(r["steps"] for r in rows) / n, 2),
            "cost_usd": round(sum(r["cost"] for r in rows), 6),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "rows": rows,
            "failures": [r for r in rows if not r["passed"]]}


# ---------------- 变异 ----------------

def validate_prompt(mutation: dict) -> tuple[bool, str]:
    if not isinstance(mutation, dict) or set(mutation) - {"plan_prompt"}:
        return False, "只接受 plan_prompt"
    text = str(mutation.get("plan_prompt") or "").strip()
    if len(text) < 10:
        return False, "策略太短"
    if len(text) > MAX_PROMPT_CHARS:
        return False, f"策略超过 {MAX_PROMPT_CHARS} 字"
    if "```" in text:
        return False, "不允许代码块"
    return True, ""


def propose_prompt(failures: list[dict], parent_prompt: str = "",
                   rows: list[dict] | None = None) -> list[dict]:
    """生成 1~2 个策略候选。

    两种目标（都来自真实测量）：
      1. 有失败 → 修正确性（补行为规则）；
      2. 全部通过 → **降本**（减少重复工具调用），这同样是产品级改进。
    """
    if not llm.configured():
        return []
    if not failures and not rows:
        return []
    if failures:
        brief = [{"q": f.get("q"), "tools": f.get("tools"),
                  "failed": f.get("failed_checks"),
                  "answer": (f.get("answer") or "")[:120]} for f in failures[:6]]
        ask = ("当前策略：" + (parent_prompt or "（无）") + chr(10) + "失败用例如下："
               + chr(10) + json.dumps(brief, ensure_ascii=False))
    else:
        heavy = sorted(rows or [], key=lambda r: -r.get("steps", 0))[:5]
        brief = [{"q": r.get("q"), "tools_used": r.get("tools"),
                  "steps": r.get("steps")} for r in heavy]
        ask = ("当前策略：" + (parent_prompt or "（无）")
               + chr(10) + "所有用例都已通过，但工具调用偏多。请写一段策略："
                 "同一个工具不要重复调用；拿到足够证据就立刻作答；不要为完整性反复检索。"
               + chr(10) + "用例：" + chr(10) + json.dumps(brief, ensure_ascii=False))
    out = []
    for _ in range(2):
        res = llm.chat_json(PROPOSE_SYSTEM, ask)
        if not res.get("ok"):
            continue
        data = res.get("data") or {}
        cand = {"plan_prompt": str(data.get("plan_prompt") or "").strip()}
        ok, _why = validate_prompt(cand)
        if ok and cand not in out:
            out.append(cand)
    return out


def gate_agent(parent_dev: float, parent_held: float, dev: dict, held: dict | None,
               cost_per_case: float, budget_left: float,
               parent_steps: float = 0.0) -> tuple[bool, str]:
    """Agent 版闸门：质量不降 + (质量提升 或 工具调用显著下降) + held-out 不退步。

    为什么允许"效率型晋级"：通过率已经 100% 时，继续只盯通过率会让进化停滞；
    真实产品里"同样答对但少调 40% 工具"就是改进。
    """
    quality_up = dev["pass_rate"] > parent_dev + 0.01
    efficiency_up = (dev["pass_rate"] >= parent_dev - 0.001
                     and parent_steps > 0
                     and dev.get("steps_mean", parent_steps) <= parent_steps * 0.8)
    if not (quality_up or efficiency_up):
        return False, (f"既没有提升通过率（{parent_dev:.0%}→{dev['pass_rate']:.0%}），"
                       f"也没有减少工具调用（{parent_steps}→{dev.get('steps_mean')}）")
    if held is None:
        return False, "缺少 held-out 结果"
    if held["pass_rate"] < parent_held - 0.01:
        return False, f"held-out 退步 {parent_held:.0%}→{held['pass_rate']:.0%}"
    if not quality_up:
        return True, "效率型晋级（通过率不降、工具调用减少）"
    if cost_per_case > 0.02:
        return False, f"单条成本过高 ${cost_per_case:.4f}"
    if budget_left < 0:
        return False, "预算耗尽"
    return True, ""


# ---------------- 主循环 ----------------

def run(generations: int = 2, budget_usd: float = 1.0, dry_run: bool = False,
        log=print) -> dict:
    dev_cases = load_cases(DEV)
    held_cases = load_cases(HELDOUT)
    if not dev_cases or not held_cases:
        return {"error": "dev/held-out 评测集缺失"}

    parent = dict(rsi.active_policy() or {})
    t0 = time.perf_counter()
    base_dev = evaluate_agent(parent, dev_cases)
    base_held = evaluate_agent(parent, held_cases)
    spent = 0.0
    log(f"  基线 gen={rsi.active_gen()} dev {base_dev['pass_rate']:.0%} / "
        f"held-out {base_held['pass_rate']:.0%}（${base_dev['cost_usd'] + base_held['cost_usd']:.4f}）")
    spent += base_dev["cost_usd"] + base_held["cost_usd"]

    parent_dev, parent_held = base_dev["pass_rate"], base_held["pass_rate"]
    parent_steps = base_dev["steps_mean"]
    gens = []
    for gen_no in range(1, max(1, generations) + 1):
        if spent > budget_usd:
            log("  预算耗尽，停止")
            break
        failures = base_dev["failures"]
        candidates = propose_prompt(failures, parent.get("plan_prompt") or "",
                                    base_dev.get("rows") or [])
        if not candidates:
            log(f"  gen {gen_no}: 没有生成有效候选")
            break
        for cand in candidates:
            child = dict(parent)
            child["plan_prompt"] = cand["plan_prompt"]
            dev = evaluate_agent(child, dev_cases)
            spent += dev["cost_usd"]
            held = None
            if dev["pass_rate"] >= parent_dev - 0.001:
                held = evaluate_agent(child, held_cases)
                spent += held["cost_usd"]
            ok, why = gate_agent(parent_dev, parent_held, dev, held,
                                 dev["cost_usd"] / max(dev["n"], 1), budget_usd - spent,
                                 parent_steps)
            res = "promoted" if ok else "rejected"
            if ok:
                parent, parent_dev, parent_held = child, dev["pass_rate"], held["pass_rate"]
                parent_steps = dev["steps_mean"]
                if not dry_run:
                    rsi._set_active(child, gen_no, dev["pass_rate"], held["pass_rate"], 0.0)
            if not dry_run:
                rsi.record(gen_no, cand, res, why, dev["pass_rate"],
                           (held or {}).get("pass_rate"), dev["cost_usd"])
            gens.append({"gen": gen_no, "result": res, "why": why,
                         "dev": dev["pass_rate"], "held": (held or {}).get("pass_rate"),
                         "prompt": cand["plan_prompt"]})
            log(f"  gen {gen_no} {res}: dev {dev['pass_rate']:.0%}"
                + (f" / held {held['pass_rate']:.0%}" if held else "（未看 held）")
                + (f" — {why}" if why else ""))
            if ok:
                break
    return {"generations": gens, "dev": parent_dev, "held": parent_held,
            "spent_usd": round(spent, 4), "gen": rsi.active_gen(),
            "seconds": round(time.perf_counter() - t0, 1),
            "policy": parent}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generations", type=int, default=2)
    ap.add_argument("--budget-usd", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = run(a.generations, a.budget_usd, a.dry_run)
    print(json.dumps({k: v for k, v in res.items() if k != "policy"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
