"""第二套真值（gold_alt）：**不按被引排序**，用来检验"权威性先验"的收益是否来自标签泄漏。

问题：第一套 gold 里 institution/topic 的 expected 是"该机构/主题下被引最高的 5 人"，
而权威性先验正是按被引重排——这等于让模型去拟合标签的构造方式，收益被高估。

这里改成：随机抽该机构/主题下的 8 位发布作者（与被引无关），
如果先验的收益仍然存在，说明它真的在提升可读性；如果大幅缩水，说明原来那部分是泄漏。
"""
from __future__ import annotations

import json
import random

from app.db import connect

OUT = "eval/gold_alt.json"


def main() -> None:
    conn = connect()
    rng = random.Random(424242)
    gold: list[dict] = []

    insts = conn.execute(
        """SELECT last_inst, COUNT(*) n FROM people
           WHERE status='published' AND last_inst IS NOT NULL
           GROUP BY last_inst HAVING n>=8 ORDER BY n DESC LIMIT 60""").fetchall()
    for r in rng.sample(insts, 25):
        rows = conn.execute(
            "SELECT pid FROM people WHERE status='published' AND last_inst=?",
            (r["last_inst"],)).fetchall()
        if len(rows) < 8:
            continue
        gold.append({"tier": "institution_random",
                     "query": f"researchers at {r['last_inst']}",
                     "kind": "people",
                     "expected": [x["pid"] for x in rng.sample(rows, 8)]})

    topics: dict[str, int] = {}
    for r in conn.execute("SELECT topics FROM people WHERE status='published'"):
        for t in json.loads(r["topics"] or "[]"):
            topics[t] = topics.get(t, 0) + 1
    hot = [t for t, n in sorted(topics.items(), key=lambda kv: -kv[1])[:60] if n >= 10]
    for t in rng.sample(hot, 25):
        rows = conn.execute(
            "SELECT pid FROM people WHERE status='published' AND topics LIKE ?",
            (f'%"{t}"%',)).fetchall()
        if len(rows) < 8:
            continue
        gold.append({"tier": "topic_random", "query": f"experts in {t}",
                     "kind": "people",
                     "expected": [x["pid"] for x in rng.sample(rows, 8)]})

    conn.close()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(gold, fh, ensure_ascii=False, indent=1)
    by: dict[str, int] = {}
    for g in gold:
        by[g["tier"]] = by.get(g["tier"], 0) + 1
    print(f"写出 {len(gold)} 条 → {OUT} 分布 {by}")


if __name__ == "__main__":
    main()
