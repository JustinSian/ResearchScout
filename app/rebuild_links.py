"""全量重建"论文—作者"关联与合作边。

背景（自查发现）：
`ingest_works.py` 在 work 已存在时用 `cur.lastrowid` 取 wid。
SQLite 的 UPSERT 走"更新"分支时不会刷新 lastrowid，于是拿到的是**上一条 INSERT**
（往往是 people 的 pid）——作者被挂到了错误的论文上。
症状：829 篇论文与别的论文共享完全相同的作者集合；钟南山的化学论文挂着新冠论文的作者。

本脚本不依赖旧数据：重新拉 40 页（8000 篇），按 openalex_id 重建：
  1. works（标题/摘要/年份/被引/venue 更新）
  2. work_authors（清空重建）
  3. people 的 works_count / topics（用论文的概念重新聚合）
  4. edges(kind='coauthor')（按每篇前 12 位作者，与采集口径一致）
"""
from __future__ import annotations

import collections
import json
import time

from app.db import connect, init_db
from app.ingest_works import PER_PAGE, PAGES, fetch_pages
from app.judge import judge


def run(pages: int = PAGES, log=print) -> dict:
    init_db()
    conn = connect()

    # 1) 先把这次要重建的 work 的 wid 全部按 openalex_id 建好索引
    wid_of: dict[str, int] = {
        r["openalex_id"]: int(r["wid"])
        for r in conn.execute("SELECT wid, openalex_id FROM works")}

    conn.execute("DELETE FROM work_authors")
    conn.execute("DELETE FROM edges WHERE kind='coauthor'")
    pid_of: dict[str, int] = {
        r["openalex_id"]: int(r["pid"])
        for r in conn.execute("SELECT pid, openalex_id FROM people WHERE openalex_id IS NOT NULL")}

    per_work: dict[int, list[int]] = {}
    topics: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    cites: collections.Counter[int] = collections.Counter()
    n_works = n_new_works = n_new_people = 0

    t0 = time.time()
    for work in fetch_pages(pages, PER_PAGE, log=log):
        oid = (work.get("id") or "").split("/")[-1]
        if not oid:
            continue
        wid = wid_of.get(oid)
        if not wid:
            cur = conn.execute(
                """INSERT INTO works(openalex_id,doi,title,abstract,year,cites,venue,type)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (oid, work.get("doi"), work.get("display_name") or "",
                 None, work.get("publication_year"), work.get("cited_by_count") or 0,
                 ((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
                 work.get("type")))
            wid = int(cur.lastrowid)
            wid_of[oid] = wid
            n_new_works += 1
        n_works += 1
        pids: list[int] = []
        for au in work.get("authorships") or []:
            a = au.get("author") or {}
            aid = (a.get("id") or "").split("/")[-1]
            if not aid:
                continue
            pid = pid_of.get(aid)
            if not pid:
                insts = au.get("institutions") or []
                inst = (insts[0].get("display_name") if insts else None) or None
                cur = conn.execute(
                    """INSERT INTO people(openalex_id,name,orcid,last_inst,status,updated_at)
                       VALUES(?,?,?,?,'pending',?)""",
                    (aid, (a.get("display_name") or "").strip(), a.get("orcid"), inst, time.time()))
                pid = int(cur.lastrowid)
                pid_of[aid] = pid
                n_new_people += 1
            if pid not in pids:
                pids.append(pid)
        per_work[wid] = pids
        wc = work.get("cited_by_count") or 0
        for c in (work.get("concepts") or [])[:5]:
            name = c.get("display_name")
            if name:
                for pid in pids:
                    topics[pid][name] += 1
        for pid in pids:
            cites[pid] += wc
        if n_works % 1000 == 0:
            conn.commit()
            log(f"  已处理 {n_works} 篇 {time.time()-t0:.0f}s")

    # 2) 写 work_authors
    rows = [(wid, pid) for wid, pids in per_work.items() for pid in pids]
    conn.executemany("INSERT OR IGNORE INTO work_authors(wid,pid) VALUES(?,?)", rows)
    conn.commit()
    log(f"  work_authors 写入 {len(rows)} 行（{len(per_work)} 篇）")

    # 3) 重建共著边（每篇前 12 位作者）
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_edges ON edges(a,b,kind)")
    pair: collections.Counter[tuple[int, int]] = collections.Counter()
    for wid, pids in per_work.items():
        sub = pids[:12]
        for i in range(len(sub)):
            for j in range(i + 1, len(sub)):
                a, b = sorted((sub[i], sub[j]))
                pair[(a, b)] += 1
    conn.executemany(
        "INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',?) "
        "ON CONFLICT(a,b,kind) DO UPDATE SET weight=excluded.weight",
        [(a, b, w) for (a, b), w in pair.items()])
    conn.commit()
    log(f"  共著边 {len(pair)} 条")

    # 4) 重新聚合 people 的 works_count / topics / cites，并重判
    pid_wids: dict[int, list[int]] = collections.defaultdict(list)
    for wid, pids in per_work.items():
        for pid in pids:
            pid_wids[pid].append(wid)
    n_judged = n_pub = 0
    all_cites = sorted(cites.values()) or [0]
    p50 = all_cites[len(all_cites) // 2]
    p90 = all_cites[min(len(all_cites) - 1, int(len(all_cites) * 0.9))]
    for r in conn.execute("SELECT pid,name,orcid,last_inst FROM people").fetchall():
        pid = int(r["pid"])
        wids = pid_wids.get(pid, [])
        tlist = [t for t, _n in topics[pid].most_common(8)] if pid in topics else []
        c = int(cites.get(pid, 0))
        feats = {"name": r["name"], "orcid": r["orcid"], "sample_works": len(wids),
                 "sample_cites": c, "cite_p50": p50, "cite_p90": p90,
                 "has_inst": r["last_inst"], "topics": tlist}
        judged = judge(feats)
        status = ("published" if judged["verdict"] == "accept"
                  else "pending" if judged["verdict"] == "review" else "rejected")
        conn.execute(
            """UPDATE people SET works_count=?, cited_by_count=?, topics=?, status=?,
                   confidence=?, evidence=?, updated_at=? WHERE pid=?""",
            (len(wids), c, json.dumps(tlist, ensure_ascii=False), status, judged["score"],
             json.dumps({"features": feats, "reasons": judged["reasons"]}, ensure_ascii=False),
             time.time(), pid))
        n_judged += 1
        n_pub += status == "published"
    conn.commit()
    conn.execute("DELETE FROM people_fts")
    for r in conn.execute("SELECT pid,name,topics,last_inst FROM people"):
        conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                     (r["pid"], r["name"], " ".join(json.loads(r["topics"] or "[]")),
                      r["last_inst"] or ""))
    conn.commit()
    conn.close()
    return {"works": n_works, "new_works": n_new_works, "new_people": n_new_people,
            "work_authors": len(rows), "edges": len(pair),
            "judged": n_judged, "published": n_pub,
            "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    print(json.dumps(run(log=lambda s: print(s, flush=True)), ensure_ascii=False))
