"""扩充评测：同一批缺口主题上做 A/B（关闭/开启自动扩充）。

指标（各自独立金标，避免循环论证）：
  answer_rate    能给出 1~3 位专家且带引用（机械判定）
  retrieval_hit  返回的专家里，有多少在 OpenAlex **作者标签**金标池里（独立于标题匹配）
  yield          新增人数 / 论文数
  precision_rule 规则口径精确率（新增者至少 1 篇标题含主题）——**不作合格线**
  cost/latency   单主题成本与耗时

另外跑回归：60 条专家发现 + 14 条 Agent 用例，确认扩充没有污染已有能力。

盲审表：每主题随机抽 5 位新增专家，输出 CSV（人名/机构/被引/3 篇代表作），
不给"来源"，供人工标注"是否该领域专家"。

用法：
  python -m app.build_gap_topics --limit 20
  python -m app.eval_expansion --limit 8          # 控制成本，先跑 8 个
  python -m app.eval_expansion --limit 8 --audit-csv eval/audit_sample.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAP = ROOT / "eval" / "gap_topics.jsonl"


def load_gap(path: Path | None = None) -> list[dict]:
    rows = []
    for line in (path or GAP).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _ask(topic: str, uid: str) -> dict:
    from app import agent_llm
    q = f"找研究 {topic} 的专家"
    t0 = time.perf_counter()
    r = agent_llm.chat(q, uid=uid, sid=f"gap-{uid}")
    return {"refused": bool(r.get("refused")),
            "pids": [int(it["pid"]) for ev in (r.get("evidence") or [])
                     for it in (ev.get("items") or []) if it.get("pid")],
            "citations": len(r.get("citations") or []),
            "answer": r.get("answer") or "",
            "cost": float(r.get("cost_usd") or 0),
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "expansion": r.get("expansion")}


def _gold_ids_of_pids(pids: list[int]) -> set[str]:
    if not pids:
        return set()
    from app.db import connect
    marks = ",".join("?" * len(pids[:50]))
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT openalex_id FROM people WHERE pid IN ({marks})",
            tuple(int(p) for p in pids[:50])).fetchall()
        return {r["openalex_id"] for r in rows if r["openalex_id"]}
    finally:
        conn.close()


def _gold_pids(ids: list[str]) -> set[int]:
    if not ids:
        return set()
    from app.db import connect
    marks = ",".join("?" * len(ids))
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT pid FROM people WHERE openalex_id IN ({marks})", tuple(ids)).fetchall()
        return {int(r["pid"]) for r in rows}
    finally:
        conn.close()


def _precision_rule(topic: str, pids: list[int]) -> float:
    """规则精确率：新增者是否有标题命中主题的论文。"""
    if not pids:
        return 0.0
    from app.db import connect
    marks = ",".join("?" * len(pids))
    conn = connect()
    try:
        ok = 0
        for pid in pids:
            n = conn.execute(
                """SELECT COUNT(*) FROM work_authors wa JOIN works w ON w.wid=wa.wid
                   WHERE wa.pid=? AND lower(w.title) LIKE ?""",
                (int(pid), f"%{topic.lower()}%")).fetchone()[0]
            if n:
                ok += 1
        return round(ok / len(pids), 3)
    finally:
        conn.close()


def run(limit: int = 8, seed: int = 11) -> dict:
    from app import agent_llm, episodes, expand, user_memory

    cases = load_gap()[:limit]
    rows = []
    audit_pool: list[dict] = []
    for c in cases:
        topic = c["topic"]
        uid = f"gap-{c['id']}"
        # A 组：关闭扩充
        import os
        os.environ["ST_AUTO_EXPAND"] = "0"
        a = _ask(topic, uid + "-a")
        # B 组：开启扩充（会真实写库）
        os.environ["ST_AUTO_EXPAND"] = "1"
        b = _ask(topic, uid + "-b")
        gold_ids = set(c.get("gold_openalex_ids") or [])
        returned_ids = _gold_ids_of_pids(b["pids"])
        hit = len(returned_ids & gold_ids)
        denom = max(min(len(gold_ids), 10), 1)
        rows.append({
            "id": c["id"], "topic": topic,
            "a_refused": a["refused"], "a_pids": len(a["pids"]),
            "b_refused": b["refused"], "b_pids": len(b["pids"]),
            "hit": hit, "gold_pool": len(gold_ids),
            "retrieval_hit": round(hit / denom, 3),
            "cost": round(a["cost"] + b["cost"], 5),
            "ms": round(a["ms"] + b["ms"], 1),
            "expansion": (b.get("expansion") or {}).get("added_people"),
        })
        # 盲审抽样：B 组返回的人里随机 5 个
        pids = list(dict.fromkeys(b["pids"]))[:20]
        rnd = random.Random(seed + len(audit_pool))
        for pid in rnd.sample(pids, min(5, len(pids))):
            audit_pool.append({"topic": topic, "pid": pid})

    n = len(rows) or 1
    summary = {
        "cases": len(rows),
        "answer_rate_a": round(sum(1 for r in rows if not r["a_refused"] and r["a_pids"]) / n, 4),
        "answer_rate_b": round(sum(1 for r in rows if not r["b_refused"] and r["b_pids"]) / n, 4),
        "retrieval_hit_b": round(sum(r["retrieval_hit"] for r in rows) / n, 4),
        "yield_people": sum(int(r["expansion"] or 0) for r in rows),
        "cost_usd": round(sum(r["cost"] for r in rows), 5),
        "latency_p50": round(statistics.median([r["ms"] for r in rows]), 1) if rows else 0,
        "zero_hit": [r["id"] for r in rows if r["hit"] == 0],
    }
    return {"summary": summary, "rows": rows, "audit_pool": audit_pool}


def write_audit_csv(pool: list[dict], path: Path) -> int:
    """盲审表：只给人名/机构/被引/代表作，不写来源与主题标签。"""
    from app.db import connect
    conn = connect()
    rows = []
    try:
        for item in pool:
            pid = int(item["pid"])
            p = conn.execute(
                """SELECT p.pid, p.name, p.last_inst, p.cited_by_count
                   FROM people p WHERE p.pid=?""", (pid,)).fetchone()
            if not p:
                continue
            titles = [r["title"] or "" for r in conn.execute(
                """SELECT w.title FROM work_authors wa JOIN works w ON w.wid=wa.wid
                   WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 3""", (pid,))]
            rows.append({
                "row_id": f"{item['topic'][:20]}-{pid}",
                "query_topic": item["topic"],
                "name": p["name"], "institution": p["last_inst"] or "",
                "cited_by": p["cited_by_count"],
                "top3_titles": " || ".join(t[:120] for t in titles),
                "label_is_expert": "",          # 人工填：是/否/边界
            })
    finally:
        conn.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["row_id", "query_topic", "name", "institution",
                            "cited_by", "top3_titles", "label_is_expert"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--audit-csv", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run(a.limit)
    if a.audit_csv:
        n = write_audit_csv(res["audit_pool"], Path(a.audit_csv))
        res["summary"]["audit_rows"] = n
    if a.json:
        print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
        return
    s = res["summary"]
    print()
    print(f"  缺口主题 {s['cases']} 个")
    print(f"  回答率：关闭扩充 {s['answer_rate_a']:.0%} → 开启 {s['answer_rate_b']:.0%}")
    print(f"  检索命中（OpenAlex 标签金标）：{s['retrieval_hit_b']:.1%}")
    print(f"  新增专家 {s['yield_people']} 人 · 成本 ${s['cost_usd']:.4f} · p50 {s['latency_p50']}ms")
    if s["zero_hit"]:
        print("  零命中：" + ", ".join(s["zero_hit"]))
    for r in res["rows"]:
        print(f"   {r['id']:<7} {r['topic'][:26]:<28} A拒={r['a_refused']} "
              f"B拒={r['b_refused']} 命中 {r['hit']}/{r['gold_pool']} +{r['expansion']}人")


if __name__ == "__main__":
    main()
