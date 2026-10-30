"""分层抽样：从 accept / review / reject 三个档各抽 20 条，供人工标注。

标注口径（与 judge 的打分规则**独立**，只看记录本身是否指向一个真实学者）：
  person     —— 是一个具体的人（姓名形态 + 有机构/作品支撑）
  non_person —— 机构、会议、期刊、主题词、"团队/委员会"等非个人实体
  borderline —— 名字像人但证据不足（单名、全大写缩写、疑似重名合并）

用法：python -m app.sample_gate_eval > eval/gate_sample.txt
"""
from __future__ import annotations

import json
import random

from app.db import connect

N_PER_BAND = 20
OUT_JSON = "eval/gate_sample.json"


def main() -> None:
    conn = connect()
    rng = random.Random(31337)
    rows = conn.execute(
        """SELECT pid, name, last_inst, works_count, cited_by_count, confidence, status
           FROM people""").fetchall()
    bands = {"accept": [], "review": [], "reject": []}
    for r in rows:
        s = float(r["confidence"] or 0)
        band = "accept" if s >= 0.80 else "review" if s >= 0.50 else "reject"
        bands[band].append(r)

    sample = []
    for band, items in bands.items():
        picked = rng.sample(items, min(N_PER_BAND, len(items)))
        for r in picked:
            sample.append({
                "pid": r["pid"], "band": band, "name": r["name"],
                "inst": r["last_inst"], "works": r["works_count"],
                "cites": r["cited_by_count"], "score": round(float(r["confidence"]), 3),
                "label": None,
            })

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(sample, fh, ensure_ascii=False, indent=1)

    print(f"# 抽样 {len(sample)} 条（每档 {N_PER_BAND}），已写 {OUT_JSON}")
    for band in bands:
        print(f"\n=== {band} ===")
        for s in [x for x in sample if x["band"] == band]:
            print(f"{s['pid']}\t{s['score']:.2f}\t{s['works']}\t{s['cites']}\t"
                  f"{(s['name'] or '')[:48]}\t{(s['inst'] or '')[:30]}")
    conn.close()


if __name__ == "__main__":
    main()
