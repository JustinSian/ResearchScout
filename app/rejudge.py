"""重判（不重新采集）：用 DB 里已有的样本统计重新跑可信判定。

为什么需要它：第一次采集后才发现"样本被引"这个信号被样本本身污染了——
我们采的是 8000 篇**最高被引**论文，所以几乎每个作者都有上千被引，
`cites>=500` 对 100% 的人都成立，accept 率被抬到 65%。

修法：把绝对阈值换成**样本内的分位数**（p50/p90），并在重判时重新算。
"""
from __future__ import annotations

import json
import statistics
import time

from app.config import GATE_ACCEPT
from app.db import connect, init_db
from app.judge import judge


def rejudge(log=print) -> dict:
    init_db()
    conn = connect()
    rows = conn.execute(
        "SELECT pid,name,orcid,last_inst,works_count,cited_by_count,topics FROM people"
    ).fetchall()
    cites = [int(r["cited_by_count"] or 0) for r in rows]
    p50 = statistics.median(cites) if cites else 0
    p90 = statistics.quantiles(cites, n=10)[-1] if len(cites) >= 10 else max(cites or [0])
    log(f"  样本被引分布: p50={p50:.0f} p90={p90:.0f}")

    accepted = review = rejected = 0
    t0 = time.time()
    # 重判会整体替换 person 候选：先清掉上一轮，否则每跑一次就多 3.6 万行
    conn.execute("DELETE FROM candidates WHERE kind='person'")
    conn.execute("DELETE FROM review_queue")
    for r in rows:
        topics = json.loads(r["topics"] or "[]")
        feats = {
            "name": r["name"], "orcid": r["orcid"],
            "sample_works": r["works_count"] or 0,
            "sample_cites": r["cited_by_count"] or 0,
            "cite_p50": p50, "cite_p90": p90,
            "has_inst": r["last_inst"], "topics": topics,
        }
        judged = judge(feats)
        # 策略：单篇作者不进发布集。依据 M3 报告——60 条人工抽样显示三档全是真人，
        # 门槛真正区分的是"产出"而非"是否是人"，所以把 work<2 的留待人工复核。
        if judged["verdict"] == "accept" and int(feats["sample_works"] or 0) >= 2:
            status = "published"
        elif judged["verdict"] == "reject":
            status = "rejected"
        else:
            status = "pending"
        conn.execute(
            "UPDATE people SET status=?, confidence=?, evidence=? WHERE pid=?",
            (status, judged["score"],
             json.dumps({"features": feats, "reasons": judged["reasons"]},
                        ensure_ascii=False), r["pid"]))
        conn.execute(
            """INSERT INTO candidates(kind,payload,evidence,score,verdict,reasons,created_at)
               VALUES('person',?,?,?,?,?,?)""",
            (json.dumps({"pid": r["pid"], "name": r["name"]}, ensure_ascii=False),
             json.dumps({"source": "rejudge"}), judged["score"], judged["verdict"],
             json.dumps(judged["reasons"], ensure_ascii=False), time.time()))
        if judged["verdict"] == "review":
            conn.execute("""INSERT OR REPLACE INTO review_queue(cid,priority)
                            VALUES(last_insert_rowid(), ?)""", (judged["score"],))
        accepted += judged["verdict"] == "accept"
        review += judged["verdict"] == "review"
        rejected += judged["verdict"] == "reject"
    conn.commit()
    conn.close()
    return {"people": len(rows), "accept": accepted, "review": review,
            "reject": rejected, "p90_cites": p90, "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    print(rejudge(log=lambda s: print(s, flush=True)))
