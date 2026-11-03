"""核心金标评测 R_core@10：只算"一作 + 标题命中概念"的人。

为什么需要它：
  现有金标来自 OpenAlex 概念标签，盲审显示**核心纯度只有 52%**（另一半是数据/工程/合著）。
  用 525 条关系算出来的 R@10 会把"相关的人"也当命中，看起来比"找到领域核心"更好。

核心金标定义（比 R@10 严格，且与检索信号尽量解耦）：
  该概念下、在库里同时满足以下两条的人：
    A. 至少 2 篇论文**标题**命中概念关键词（与检索用的标题匹配同源，这是已知的循环性）；
    B. 其中至少 1 篇是**第一作者**（这一条是检索不使用的独立信号）。

注意：A 与检索同源，所以 R_core 不能当作"独立验证"，只能回答
"在标题能匹配的人里，第一作者是否被排到前面"。

用法：
  python -m app.eval_core            # 输出 R_core@10 与覆盖
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "expert_gold.jsonl"

STOP = {"and", "the", "of", "for", "with", "using", "based", "analysis", "system",
        "systems", "model", "models", "data", "study", "research", "method",
        "methods", "approach", "theory", "application", "applications"}


def keywords(concept: str) -> list[str]:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]+", concept or "")]
    return [t for t in toks if t not in STOP]


def core_pids(conn, concept: str) -> list[int]:
    kws = keywords(concept)
    if not kws:
        return []
    like = " OR ".join(["lower(w.title) LIKE ?"] * len(kws))
    args = [f"%{k}%" for k in kws]
    rows = conn.execute(
        f"""SELECT wa.pid,
                   SUM(CASE WHEN wa.position = 0 THEN 1 ELSE 0 END) AS first_auth,
                   COUNT(*) AS hits
            FROM work_authors wa JOIN works w ON w.wid = wa.wid
            WHERE {like}
            GROUP BY wa.pid
            HAVING hits >= 2 AND first_auth >= 1""", args).fetchall()
    return [int(r["pid"]) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()
    from app.agent import tool_find_expert
    from app.db import connect

    # 数据守卫：主库的 work_authors.position 几乎全是 NULL，"一作"不可用。
    # 不检查就会得到 R_core=0% 的假结论（实测踩过）。
    _c = connect()
    try:
        total = _c.execute("SELECT COUNT(*) FROM work_authors").fetchone()[0]
        with_pos = _c.execute(
            "SELECT COUNT(*) FROM work_authors WHERE position IS NOT NULL").fetchone()[0]
    finally:
        _c.close()
    if total and with_pos / total < 0.5:
        print(f"  R_core 不可计算：work_authors 有 position 的只占 {with_pos/total:.1%}"
              f"（{with_pos}/{total}），缺少作者位次数据")
        print("  需要先回填 position（从 OpenAlex authorships 重新导入），或改用不含位次的定义")
        return

    gold = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    conn = connect()
    recalls, covered, total = [], 0, 0
    try:
        for g in gold:
            concept = g.get("concept") or g.get("query") or ""
            core = core_pids(conn, concept)
            if not core:
                continue
            total += 1
            try:
                res = tool_find_expert(concept, limit=a.limit)
            except Exception:
                res = {"items": []}
            hit = [int(it["pid"]) for it in (res.get("items") or []) if it.get("pid")]
            r = len(set(hit[:a.limit]) & set(core)) / len(core)
            recalls.append(r)
            if r > 0:
                covered += 1
    finally:
        conn.close()

    n = len(recalls) or 1
    print(f"  概念 {len(gold)} 个，其中有核心金标 {total} 个")
    print(f"  R_core@10 {sum(recalls)/n:.1%} · 覆盖 {covered/n:.0%}")
    print("  注意：核心金标用标题匹配构造，与检索同源（循环性），只作补充指标")


if __name__ == "__main__":
    main()
