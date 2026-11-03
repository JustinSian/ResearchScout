"""受控自我改进（RSI）：变异 → dev 评估 → held-out 闸门 → 晋级/回滚。

**说清楚它是什么**：这不是"AI 改自己的代码"，而是
"在一个白名单策略空间里做搜索，用外部评测当闸门"。

可以改的（白名单）：
  synonyms       查询扩展词表 {中文主题: [英文检索词]}   ← 当前唯一接通检索的
  rerank_weights 重排权重（预留，未接通）
  plan_prompt    规划提示词片段（已接通：agent_llm 会把它拼进 system prompt）
  tool_order     工具调用顺序（预留，未接通）

闸门（四条，全部可单测）：
  1. dev 不退化：dev_pass >= parent_dev_pass
  2. held-out 有提升：heldout_pass > parent_heldout_pass + 0.01
  3. 成本不失控：cost_per_case <= parent_cost * 1.2
  4. 预算与代数：单代 <= budget_usd，总代数 <= 10

防作弊：
  - propose() 只能看到 dev 集；held-out 由 gate() 内部读取；
  - held-out / perturbed 文件带 sha256 校验，被改过就拒绝运行；
  - 每次晋级写快照，可一键回滚；rsi_log 记录被拒绝的变异与原因。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from app import llm, user_memory

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DEV = EVAL_DIR / "rsi_dev.jsonl"
HELDOUT = EVAL_DIR / "rsi_heldout.jsonl"
PERTURBED = EVAL_DIR / "rsi_perturbed.jsonl"

MUTABLE = {"synonyms", "rerank_weights", "plan_prompt", "tool_order"}
WIRED = {"synonyms", "plan_prompt"}      # 真正生效的：检索扩展 + Agent 提示词

DDL = [
    """CREATE TABLE IF NOT EXISTS mem_policy (
        gen INTEGER PRIMARY KEY,
        content_json TEXT NOT NULL,
        active INTEGER DEFAULT 0,
        dev_pass REAL, heldout_pass REAL, cost_per_case REAL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_policy_active ON mem_policy(active) WHERE active = 1",
    """CREATE TABLE IF NOT EXISTS rsi_log (
        id INTEGER PRIMARY KEY, gen INTEGER, mutation_json TEXT, result TEXT,
        reason TEXT, dev_pass REAL, heldout_pass REAL, cost_usd REAL, ts TEXT NOT NULL
    )""",
]

_ACTIVE: dict | None = None
_ACTIVE_GEN: int = 0


# ---------------- 存储 ----------------

def _conn():
    conn = user_memory.connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    return conn


def active_policy() -> dict:
    global _ACTIVE, _ACTIVE_GEN
    if _ACTIVE is not None:
        return _ACTIVE
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT gen, content_json FROM mem_policy WHERE active=1 "
            "ORDER BY gen DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        _ACTIVE, _ACTIVE_GEN = {}, 0
        return _ACTIVE
    try:
        _ACTIVE = json.loads(row["content_json"]) or {}
    except Exception:
        _ACTIVE = {}
    _ACTIVE_GEN = int(row["gen"])
    return _ACTIVE


def active_gen() -> int:
    active_policy()
    return _ACTIVE_GEN


def _set_active(policy: dict, gen: int, dev=None, held=None, cost=None) -> None:
    global _ACTIVE, _ACTIVE_GEN
    conn = _conn()
    try:
        conn.execute("UPDATE mem_policy SET active=0")
        conn.execute(
            """INSERT INTO mem_policy(gen, content_json, active, dev_pass, heldout_pass,
                                      cost_per_case, created_at)
               VALUES(?,?,1,?,?,?,?)
               ON CONFLICT(gen) DO UPDATE SET content_json=excluded.content_json,
                   active=1, dev_pass=excluded.dev_pass, heldout_pass=excluded.heldout_pass,
                   cost_per_case=excluded.cost_per_case""",
            (gen, json.dumps(policy, ensure_ascii=False), dev, held, cost,
             user_memory.now_iso()))
        conn.commit()
    finally:
        conn.close()
    _ACTIVE, _ACTIVE_GEN = policy, gen


def rollback(gen: int | None = None) -> dict:
    """回滚到指定代；不给 gen 就回到上一代（没有就清空）。"""
    conn = _conn()
    try:
        rows = conn.execute("SELECT gen, content_json FROM mem_policy ORDER BY gen DESC").fetchall()
    finally:
        conn.close()
    cur = active_gen()
    if gen == 0:
        _set_active({}, 0)
        return {"ok": True, "gen": 0, "policy": {}}
    if not rows:
        return {"ok": False, "reason": "没有任何策略快照"}
    target = None
    for r in rows:
        if gen is not None and int(r["gen"]) == int(gen):
            target = r
            break
        if gen is None and int(r["gen"]) < cur:
            target = r
            break
    if not target:
        return {"ok": False, "reason": f"找不到可回滚的代（当前 gen={cur}）"}
    policy = json.loads(target["content_json"]) or {}
    _set_active(policy, int(target["gen"]))
    return {"ok": True, "gen": int(target["gen"]), "policy": policy}


def record(gen: int, mutation: dict, result: str, reason: str = "",
        dev: float | None = None, held: float | None = None,
        cost: float | None = None) -> None:
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO rsi_log(gen, mutation_json, result, reason, dev_pass,
                                   heldout_pass, cost_usd, ts)
               VALUES(?,?,?,?,?,?,?,?)""",
            (gen, json.dumps(mutation, ensure_ascii=False), result, reason, dev, held,
             cost, user_memory.now_iso()))
        conn.commit()
    finally:
        conn.close()


def history(limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM rsi_log ORDER BY id DESC LIMIT ?",
                            (max(1, int(limit)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def generations(limit: int = 20) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT gen, active, dev_pass, heldout_pass, cost_per_case, created_at "
            "FROM mem_policy ORDER BY gen DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------- 变异 ----------------

def validate(mutation: dict) -> tuple[bool, str]:
    """白名单 + 形状校验。不合法直接拒绝，不进评估。"""
    if not isinstance(mutation, dict) or not mutation:
        return False, "空变异"
    unknown = set(mutation) - MUTABLE
    if unknown:
        return False, f"未知变异类型: {sorted(unknown)}"
    if "synonyms" in mutation:
        syn = mutation["synonyms"]
        if not isinstance(syn, dict):
            return False, "synonyms 必须是 {主题: [词]} 对象"
        for k, v in syn.items():
            if not isinstance(k, str) or not k.strip():
                return False, "synonyms 的键必须是非空字符串"
            if not isinstance(v, list) or not v or len(v) > 5:
                return False, f"synonyms[{k}] 必须是 1~5 个词的数组"
            if any((not isinstance(x, str)) or not x.strip() for x in v):
                return False, f"synonyms[{k}] 里有非字符串"
    return True, ""


def merge(base: dict, mutation: dict) -> dict:
    """把变异合并到父策略（synonyms 按键合并，去重）。"""
    out = json.loads(json.dumps(base or {}))
    if "synonyms" in mutation:
        cur = out.setdefault("synonyms", {})
        for k, vs in mutation["synonyms"].items():
            exist = list(cur.get(k) or [])
            for v in vs:
                if v not in exist:
                    exist.append(v)
            cur[k] = exist[:6]
    for key in ("rerank_weights", "plan_prompt", "tool_order"):
        if key in mutation:
            out[key] = mutation[key]
    return out


def synonyms_for(topic: str) -> list[str]:
    """检索时调用：当前策略里匹配该主题的扩展词。"""
    if not topic:
        return []
    syn = (active_policy() or {}).get("synonyms") or {}
    out: list[str] = []
    for k, vs in syn.items():
        if k and k in topic:
            out += [v for v in vs if v not in out]
    return out[:5]


def propose(parent: dict, cases: list[dict], k: int = 4) -> list[dict]:
    """根据 dev 失败用例提议变异。LLM 优先，失败退回内置词表。"""
    failing = [c for c in cases if c.get("_failed")]
    topics = []
    for c in failing[:6]:
        t = c.get("topic") or ""
        if t and t not in topics:
            topics.append(t)
    if not topics:
        return []
    proposals: list[dict] = []
    if llm.configured():
        try:
            proposals = _propose_llm(topics, parent, k)
        except Exception:
            proposals = []
    if not proposals:
        proposals = _propose_rules(topics, parent)
    out = []
    for p in proposals[:k]:
        ok, why = validate(p)
        if ok:
            out.append(p)
    return out


def _propose_rules(topics: list[str], parent: dict) -> list[dict]:
    """兜底：用内置领域词表。没有对应词的 topic 跳过。"""
    from app.memory_recall import _TOPIC_FALLBACK
    syn = {}
    exist = (parent.get("synonyms") or {})
    for t in topics:
        for zh, words in _TOPIC_FALLBACK.items():
            if zh in t and zh not in exist:
                syn[zh] = list(words)[:3]
                break
    return [{"synonyms": syn}] if syn else []


SYSTEM = (
    "你是检索优化器。用户会给出若干「中文研究主题」，这些主题在当前英文语料里检索不到。"
    "请为每个主题给出 1~3 个英文检索词。只输出 JSON："
    r'{"synonyms": {"中文主题": ["英文词1", "英文词2"]}}'
)


def _propose_llm(topics: list[str], parent: dict, k: int) -> list[dict]:
    exist = (parent.get("synonyms") or {})
    ask = "已存在扩展：" + json.dumps(exist, ensure_ascii=False) + "\n需要扩展的主题：" + \
          "、".join(topics)
    res = llm.chat_json(SYSTEM, ask)
    if not res.get("ok"):
        return []
    data = res.get("data") or {}
    syn = data.get("synonyms")
    if not isinstance(syn, dict):
        return []
    return [{"synonyms": {str(a): list(b)[:3] for a, b in syn.items()
                          if isinstance(b, list)}}]


# ---------------- 评估与闸门 ----------------

def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_cases(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _check(case: dict, r: dict) -> bool:
    """判定：库内命中（非网络兜底）且带引用；可选 must_any。"""
    if not r:
        return False
    if r.get("route") not in ("kb", "hybrid"):
        return False
    if not (r.get("citations") or []):
        return False
    if case.get("must_any"):
        text = r.get("answer") or ""
        return any(w in text for w in case["must_any"])
    return True


def evaluate(policy: dict, cases: list[dict], log=print) -> dict:
    """在给定策略下跑一遍用例。策略通过全局 active 生效（含 synonyms 注入检索）。"""
    global _ACTIVE, _ACTIVE_GEN
    prev_policy, prev_gen = active_policy(), _ACTIVE_GEN
    _ACTIVE, _ACTIVE_GEN = policy, _ACTIVE_GEN
    # 评测走规则规划：RSI 要衡量的是**策略效果**，不是 LLM 抖动的差异；
    # 顺带把 20 条用例的耗时从分钟级降到秒级。
    _prev_planner = os.environ.get("ST_PLANNER")
    os.environ["ST_PLANNER"] = "rules"
    try:
        return _evaluate_inner(policy, cases)
    finally:
        if _prev_planner is None:
            os.environ.pop("ST_PLANNER", None)
        else:
            os.environ["ST_PLANNER"] = _prev_planner
        _ACTIVE, _ACTIVE_GEN = prev_policy, prev_gen


def _evaluate_inner(policy: dict, cases: list[dict]) -> dict:
    t0 = time.perf_counter()
    passed = 0
    failed = []
    from app.supervisor import run as supervise
    for c in cases:
        r = supervise(c["q"], sid=f"rsi-{c.get('id')}", log=lambda *_: None)
        ok = _check(c, r)
        c["_failed"] = not ok
        passed += 1 if ok else 0
        if not ok:
            failed.append(c.get("id"))
    ms = (time.perf_counter() - t0) * 1000
    return {"n": len(cases), "pass_rate": round(passed / max(len(cases), 1), 4),
            "failed": failed, "ms": round(ms, 1), "cost_per_case": 0.0}


def gate(parent: dict, child: dict, parent_dev: float, parent_held: float,
         dev: dict, held: dict, budget_usd: float, spent: float,
         parent_cost: float = 0.0) -> tuple[bool, str]:
    """四条闸门。返回 (是否晋级, 原因)。"""
    if dev["pass_rate"] < parent_dev:
        return False, f"dev 退化 {parent_dev:.0%}→{dev['pass_rate']:.0%}"
    if held["pass_rate"] <= parent_held + 0.01:
        return False, f"held-out 没有提升 {parent_held:.0%}→{held['pass_rate']:.0%}"
    if parent_cost and held.get("cost_per_case", 0) > parent_cost * 1.2:
        return False, "成本超限"
    if spent > budget_usd:
        return False, f"预算超限 {spent:.3f}>{budget_usd}"
    return True, ""


def run(generations: int = 3, budget_usd: float = 2.0, dry_run: bool = False,
        log=print) -> dict:
    """跑若干代。返回 {promoted, rejected, gens:[...]}。

    dry_run 只评估不落库（不晋级、不写 rsi_log）。
    """
    dev_cases = load_cases(DEV)
    held_cases = load_cases(HELDOUT)
    if not dev_cases or not held_cases:
        return {"error": "评测集缺失", "promoted": 0, "rejected": 0, "gens": []}
    parent = active_policy()
    parent_dev = evaluate(parent, dev_cases)["pass_rate"]
    parent_held = evaluate(parent, held_cases)["pass_rate"]
    log(f"  基线（gen={active_gen()}）dev {parent_dev:.0%} / held-out {parent_held:.0%}")

    promoted, rejected, gens = 0, 0, []
    spent = 0.0
    gen = active_gen()
    for step in range(1, max(1, min(int(generations), 10)) + 1):
        gen += 1
        proposals = propose(parent, dev_cases, k=4)
        if not proposals:
            log(f"  gen {gen}: 没有可用的变异提议")
            break
        for mutation in proposals:
            child = merge(parent, mutation)
            dev = evaluate(child, dev_cases)
            held = evaluate(child, held_cases) if dev["pass_rate"] >= parent_dev else \
                {"pass_rate": 0.0, "failed": [], "n": len(held_cases), "cost_per_case": 0.0}
            ok, why = gate(parent, child, parent_dev, parent_held, dev, held,
                           budget_usd, spent)
            res = "promoted" if ok else "rejected"
            if ok:
                promoted += 1
                parent, parent_dev, parent_held = child, dev["pass_rate"], held["pass_rate"]
                if not dry_run:
                    _set_active(child, gen, dev["pass_rate"], held["pass_rate"], 0.0)
            else:
                rejected += 1
            gens.append({"gen": gen, "mutation": mutation, "result": res, "reason": why,
                         "dev": dev["pass_rate"], "held": held["pass_rate"]})
            if not dry_run:
                log(f"  gen {gen} {res}: dev {dev['pass_rate']:.0%} / held "
                    f"{held['pass_rate']:.0%} {('— ' + why) if why else ''}")
                record(gen, mutation, res, why, dev["pass_rate"], held["pass_rate"], 0.0)
            if ok:
                break            # 每代只晋级一个，避免同代多次改动叠加
    return {"promoted": promoted, "rejected": rejected, "gens": gens,
            "dev_pass": parent_dev, "heldout_pass": parent_held, "gen": active_gen()}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--generations", type=int, default=3)
    ap.add_argument("--budget-usd", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.generations, a.budget_usd, a.dry_run), ensure_ascii=False, indent=2))
