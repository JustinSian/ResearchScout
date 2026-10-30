"""M3 分析：门槛到底在做什么？

人工抽样 60 条（每档 20）的结论：accept / review / reject 三档**全是真实人名**，
没有机构名、会议名混进来。原因是采集走 authorship（论文作者列表），源头本来就是人。

所以"person vs non-person 的精度"这个指标在这里没有区分度——
真正要回答的是：
  1) 库里到底有没有非人实体？门槛有没有把它们挡住？
  2) 门槛把"够格专家"（样本内 ≥2 篇）挡掉多少（漏检率）？
  3) 待审队列里有多少其实是够格专家（浪费复核产能）？
"""
from __future__ import annotations

import json
import re

from app.db import connect

NON_PERSON = re.compile(
    r"\b(universit|institut|college|school|department|faculty|academy|society|"
    r"association|conference|proceedings|symposium|workshop|journal|series|"
    r"ministry|agency|council|center|centre|laborator|hospital|foundation|"
    r"committee|federation|consortium|group|team)\b", re.I)


def main() -> None:
    conn = connect()
    total = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    pub = conn.execute("SELECT COUNT(*) FROM people WHERE status='published'").fetchone()[0]
    rev = conn.execute("SELECT COUNT(*) FROM people WHERE status='pending'").fetchone()[0]
    rej = conn.execute("SELECT COUNT(*) FROM people WHERE status='rejected'").fetchone()[0]

    print("== 1. 库内是否存在非人实体 ==")
    rows = conn.execute("SELECT pid, name, status, confidence FROM people").fetchall()
    non_person = [(r["pid"], r["name"], r["status"], r["confidence"])
                  for r in rows if NON_PERSON.search(r["name"] or "")]
    print(f"  名字命中机构/会议词的人数: {len(non_person)} / {total}")
    for p in non_person[:8]:
        print(f"    pid={p[0]:<6} {str(p[1])[:44]:<44} status={p[2]:<10} score={p[3]}")
    caught = sum(1 for p in non_person if p[2] != "published")
    if non_person:
        print(f"  其中被挡下（非 published）: {caught}/{len(non_person)} "
              f"= {caught/len(non_person):.1%}")

    print("\n== 2. '够格专家'（样本内 ≥2 篇）的三档分布 ==")
    for label, cond in (("works>=2", "works_count>=2"), ("works>=3", "works_count>=3"),
                        ("works>=5", "works_count>=5")):
        tot = conn.execute(f"SELECT COUNT(*) FROM people WHERE {cond}").fetchone()[0]
        p = conn.execute(f"SELECT COUNT(*) FROM people WHERE {cond} AND status='published'").fetchone()[0]
        v = conn.execute(f"SELECT COUNT(*) FROM people WHERE {cond} AND status='pending'").fetchone()[0]
        r = conn.execute(f"SELECT COUNT(*) FROM people WHERE {cond} AND status='rejected'").fetchone()[0]
        print(f"  {label:<9} 总 {tot:>6} | published {p:>6} ({p/max(tot,1):.1%}) "
              f"| pending {v:>6} | rejected {r:>6}")

    print("\n== 3. published 里有多少只是单篇（精度视角） ==")
    single = conn.execute(
        "SELECT COUNT(*) FROM people WHERE status='published' AND works_count<2").fetchone()[0]
    print(f"  published 且 works<2: {single}/{pub} = {single/max(pub,1):.1%}")

    print("\n== 4. 待审队列里有多少是够格专家（浪费复核产能） ==")
    good_in_review = conn.execute(
        "SELECT COUNT(*) FROM people WHERE status='pending' AND works_count>=2").fetchone()[0]
    print(f"  pending 且 works>=2: {good_in_review}/{rev} = {good_in_review/max(rev,1):.1%}")

    print("\n== 5. 人工抽样 60 条的结果 ==")
    try:
        sample = json.load(open("eval/gate_sample.json", encoding="utf-8"))
        by: dict[str, dict[str, int]] = {}
        for s in sample:
            band = s["band"]
            by.setdefault(band, {"n": 0, "works1": 0})
            by[band]["n"] += 1
            by[band]["works1"] += int((s.get("works") or 0) < 2)
        print("  标注结论：三档 60 条全部是真实人名（0 条机构/会议名）")
        print("  各档里 works<2 的比例（门槛实际在区分'产出'而非'是否是人'）：")
        for b, v in by.items():
            print(f"    {b:<8} n={v['n']:<3} works<2 占 {v['works1']/max(v['n'],1):.0%}")
    except FileNotFoundError:
        print("  （未找到 eval/gate_sample.json）")
    conn.close()


if __name__ == "__main__":
    main()
