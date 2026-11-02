"""verifier 的故障注入实验：多智能体里最该被量化的一环。

问题：task 流程里有 researcher → analyst → verifier 三个角色，
但"verifier 到底拦住了什么"一直没测过。

做法（不依赖 LLM，可控可复现）：
1. 用真实档案构造 **干净 claim**（metric / term / domain / coauthor 四类）；
2. 对每类做**故障注入**（数值改 1、术语换成无关词、主题换成别的领域、共同论文数改大）；
3. 分别喂给 `subagents.verifier`，统计：
   - `detect`：被注入的错误有没有被拒（检出率）
   - `false_alarm`：干净 claim 有没有被误拒（误报率，必须为 0）
   - `latency`：每 100 条 claim 的额外耗时

对照组 = "没有 verifier"：所有 claim 直接入库，检出率恒为 0。
所以这里的核心数字是 **检出率 × 四类**，以及**误报率是否为 0**。

用法：python -m app.ab_verifier [--pids 527,503] [--n 5]
"""
from __future__ import annotations

import argparse
import statistics
import time

from app.db import connect
from app.dossiers import dossier
from app.subagents import verifier


def _pick_pids(limit: int = 3) -> list[int]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT pid FROM dossier ORDER BY works_loaded DESC LIMIT ?", (limit,)).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


def _clean_claims(pid: int) -> list[dict]:
    d = dossier(pid) or {}
    m = d.get("metrics") or {}
    out: list[dict] = []
    for field in ("works", "cites", "h_index"):
        if isinstance(m.get(field), (int, float)):
            out.append({"kind": "metric", "pid": pid, "field": field, "value": m[field]})
    titles = [p.get("title") or "" for p in (d.get("papers") or [])]
    word = ""
    for t in titles:
        for w in t.split():
            if len(w) > 6 and w.isalpha():
                word = w
                break
        if word:
            break
    if word:
        out.append({"kind": "term", "pid": pid, "term": word})
    # 取一个真实共同作者，shared 用边权重
    conn = connect()
    try:
        row = conn.execute(
            """SELECT CASE WHEN a=? THEN b ELSE a END AS other, weight
               FROM edges WHERE kind='coauthor' AND (a=? OR b=?)
               ORDER BY weight DESC LIMIT 1""", (pid, pid, pid)).fetchone()
    finally:
        conn.close()
    if row:
        out.append({"kind": "coauthor", "a": pid, "b": int(row[0]),
                    "shared": int(row[1])})
    # 领域 claim 必须用这个人**自己的**真实主题词，否则误报是测试造出来的，不是 verifier 的问题
    terms = _real_terms(pid)
    if terms:
        out.append({"kind": "domain", "pid": pid, "topic": terms, "min_hits": 1})
    return out


def _real_terms(pid: int, k: int = 2) -> list[str]:
    from app.harness import _term_profile
    d = dossier(pid) or {}
    last = ((d.get("metrics") or {}).get("last_year")) or 2026
    prof = _term_profile(pid, last - 4, last + 1, top=30)
    words = [w for w, _n in prof if len(w) > 3]
    return words[:k]


def _corrupt(claim: dict, mode: str = "nonsense", other_terms: list[str] | None = None) -> dict:
    """mode="nonsense"：换成明显无关的内容；mode="cross"：换成**另一个人真实的主题**。

    cross 是难题：关键词本身真实存在，只是"不是这个人的"，用来测区分能力而不是测拼写。
    """
    c = dict(claim)
    kind = c["kind"]
    if kind == "metric":
        c["value"] = (c["value"] or 0) + 1
    elif kind == "term":
        c["term"] = "zzzznonexistentterm"
    elif kind == "coauthor":
        c["shared"] = (c.get("shared") or 0) + 3
    elif kind == "domain":
        if mode == "cross" and other_terms:
            c["topic"] = list(other_terms)
        else:
            c["topic"] = ["medieval", "poetry"]
        c["min_hits"] = 1
    return c


def run(pids: list[int] | None = None, n: int = 5, log=print) -> dict:
    pids = pids or _pick_pids(3)
    clean: list[dict] = []
    for pid in pids:
        clean += _clean_claims(pid)
    clean = clean[: max(n * 4, 8)]

    # 两条注入线：无意义词（简单）/ 换成**另一个人**的真实主题（难）
    # 注意：必须按 claim 的 pid 换"别人"的词。第一版用全局 others[:2]，
    # 结果对第一个人注入的是他自己的词 —— 那不是跨人，必然漏检（实验 bug）。
    terms_by_pid = {p: _real_terms(p, 2) for p in pids}

    def _other_terms(pid: int) -> list[str]:
        for q, t in terms_by_pid.items():
            if q != pid and t:
                return list(t)
        return ["medieval", "poetry"]

    dirty_nonsense = [_corrupt(c, "nonsense") for c in clean]
    dirty_cross = [_corrupt(c, "cross", _other_terms(int(c.get("pid") or 0)))
                   for c in clean]

    # 预热：_term_stats 是一次性全表扫描，不该算进"每条 claim 的成本"
    try:
        from app.subagents import _term_stats
        _term_stats()
    except Exception:
        pass

    def _timed(fn, claims, warm: bool = False):
        if warm:
            fn(claims, log=lambda *_: None)
        t0 = time.perf_counter()
        r = fn(claims, log=lambda *_: None)
        return r, (time.perf_counter() - t0) * 1000

    v_clean, t_clean = _timed(verifier, clean, warm=True)
    v_non, t_non = _timed(verifier, dirty_nonsense)
    v_cross, t_cross = _timed(verifier, dirty_cross)

    kinds = sorted({c["kind"] for c in clean})

    def detect(result: dict, kind: str, claims: list[dict]) -> float:
        total = sum(1 for c in claims if c["kind"] == kind)
        hit = sum(1 for c in result["rejected"] if c["kind"] == kind)
        return round(hit / max(total, 1), 3)

    stats = {
        "pids": pids,
        "claims_clean": len(clean),
        "false_alarm_rate": round(len(v_clean["rejected"]) / max(len(clean), 1), 3),
        "clean_pass_rate": v_clean["pass_rate"],
        "detect_by_kind": {
            "nonsense": {k: detect(v_non, k, dirty_nonsense) for k in kinds},
            "cross": {k: detect(v_cross, k, dirty_cross) for k in kinds},
        },
        "detect_overall": {
            "nonsense": round(len(v_non["rejected"]) / max(len(dirty_nonsense), 1), 3),
            "cross": round(len(v_cross["rejected"]) / max(len(dirty_cross), 1), 3),
        },
        "latency_ms": {"clean": round(t_clean, 1), "nonsense": round(t_non, 1),
                       "cross": round(t_cross, 1)},
        "latency_ms_per_100": round(t_clean / max(len(clean), 1) * 100, 1),
        "rejected_clean_examples": v_clean["rejected"][:3],
        "missed_cross_examples": [c for c in dirty_cross
                                  if c["kind"] == "domain"
                                  and not any(r.get("pid") == c.get("pid")
                                              for r in v_cross["rejected"])][:2],
    }
    log(f"  干净 {len(clean)} 条 → 误报 {stats['false_alarm_rate']:.0%}"
        f"（通过 {stats['clean_pass_rate']:.0%}）")
    for mode in ("nonsense", "cross"):
        log(f"  注入[{mode}] 检出 {stats['detect_overall'][mode]:.0%}  "
            + "  ".join(f"{k}={v:.0%}" for k, v in stats["detect_by_kind"][mode].items()))
    log(f"  每 100 条耗时 {stats['latency_ms_per_100']:.0f}ms")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pids", default="")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()
    pids = [int(x) for x in a.pids.split(",") if x.strip()] or None
    s = run(pids, n=a.n)
    print()
    print(f"  误报率 {s['false_alarm_rate']:.0%}"
          f" · 检出(无意义) {s['detect_overall']['nonsense']:.0%}"
          f" · 检出(跨人) {s['detect_overall']['cross']:.0%}"
          f" · 每 100 条 {s['latency_ms_per_100']:.0f}ms")


if __name__ == "__main__":
    main()
