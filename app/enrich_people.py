"""给"某个专家网络"里的人补真实档案（OpenAlex），并按新证据重新判定。

场景：钟南山的 111 位共著者大多是"只出现过一次"的记录，
没有机构、没有作品数，点开画像一片空白——网络看着有节点但没有内容。

做法：
1. 批量按 OpenAlex author id 取档案（一次 50 个，111 人只需 3 次请求）；
2. 更新 姓名/ORCID/机构/作品数/被引/主题；
3. 重新跑可信判定——新增一条"网络背书"证据（见 judge.py）；
4. 只给 works_count >= 10 的人这条证据，避免"一篇大协作论文"把人抬上去。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.db import connect, init_db
from app.judge import judge
from app.labels import label_topic
from app.openalex import BASE, MAILTO, UA, disable_key, with_key

SELECT = "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,x_concepts"


def _fetch_batch(openalex_ids: list[str]) -> list[dict]:
    filt = "ids.openalex:" + "|".join(openalex_ids)
    url = f"{BASE}/authors?filter={urllib.parse.quote(filt)}&per_page=50&select={SELECT}&mailto={MAILTO}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(with_key(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return (json.loads(r.read().decode()) or {}).get("results") or []
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                disable_key("403"); continue
            if exc.code in (401, 429, 500, 502, 503) and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            return []
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            return []
    return []


def enrich_network(center_pid: int, log=print) -> dict:
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT DISTINCT p.pid, p.openalex_id FROM edges e
           JOIN people p ON p.pid = CASE WHEN e.a=? THEN e.b ELSE e.a END
           WHERE (e.a=? OR e.b=?) AND e.kind='coauthor'
             AND p.openalex_id IS NOT NULL AND p.openalex_id NOT LIKE 'ext:%'""",
        (center_pid, center_pid, center_pid)).fetchall()
    log(f"  网络内 {len(rows)} 人，需补档案")
    ids = [r["openalex_id"].split("/")[-1] for r in rows]
    pid_of = {r["openalex_id"].split("/")[-1]: int(r["pid"]) for r in rows}

    updated = published = 0
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        for a in _fetch_batch(batch):
            oid = (a.get("id") or "").rsplit("/", 1)[-1]
            pid = pid_of.get(oid)
            if not pid:
                continue
            insts = a.get("last_known_institutions") or []
            inst = (insts[0].get("display_name") if insts else None)
            topics = [c.get("display_name") for c in (a.get("x_concepts") or [])[:8]
                      if c.get("display_name")]
            works = int(a.get("works_count") or 0)
            cites = int(a.get("cited_by_count") or 0)
            feats = {"name": a.get("display_name") or "", "orcid": a.get("orcid"),
                     "sample_works": works, "sample_cites": cites,
                     "cite_p50": 5504, "cite_p90": 18368,
                     "has_inst": inst, "topics": topics,
                     "coauthor_of_verified": works >= 10}
            judged = judge(feats)
            status = ("published" if judged["verdict"] == "accept"
                      else "pending" if judged["verdict"] == "review" else "rejected")
            conn.execute(
                """UPDATE people SET name=?, orcid=COALESCE(?,orcid), last_inst=CASE WHEN source='huafu' THEN last_inst ELSE COALESCE(?,last_inst) END,
                       works_count=?, cited_by_count=?, topics=?, status=?, confidence=?,
                       evidence=?, updated_at=? WHERE pid=?""",
                (a.get("display_name") or "", a.get("orcid"), inst, works, cites,
                 json.dumps(topics, ensure_ascii=False), status, judged["score"],
                 json.dumps({"features": feats, "reasons": judged["reasons"],
                             "source": f"network_of:{center_pid}"}, ensure_ascii=False),
                 time.time(), pid))
            # 主题里的英文概念也顺手补中文，画像页直接可读
            conn.execute("DELETE FROM people_fts WHERE rowid=?", (pid,))
            conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                         (pid, (a.get("display_name") or ""),
                          " ".join(topics + [label_topic(t) for t in topics]), inst or ""))
            updated += 1
            published += status == "published"
        conn.commit()
        time.sleep(0.5)
    conn.close()
    log(f"  更新 {updated} 人，其中判定为已发布 {published}")
    return {"scanned": len(rows), "updated": updated, "published": published}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    a = ap.parse_args()
    print(enrich_network(a.pid))
