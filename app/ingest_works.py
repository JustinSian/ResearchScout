"""按论文采集（works-first）。

为什么换方向：OpenAlex 无 key 时按 IP 共享每日 $0.10 预算，**每请求固定 $0.0001、
与 per_page 无关**。所以「一人一请求」最多 1000 人/天；而「一页 200 篇论文」的
authorships 里就带着几百位作者，同样 1 个 credit。第一次试跑就是因为逐人拉 works
把预算打光（401）。

流程：
  1) 翻页取高被引论文（含 authorships / concepts / abstract）
  2) 从 authorship 建人（姓名/ORCID/机构），建合作边
  3) 用"样本内作品数/被引/机构/主题"做可信判定（judge），写 candidates
"""
from __future__ import annotations

import collections
import json
import time

from app.config import GATE_ACCEPT
from app.db import connect, init_db, rebuild_fts, set_meta
from app.judge import judge
from app.openalex import abstract_from_inverted

PAGES = 40              # 40 × 200 = 8000 篇
PER_PAGE = 200
SELECT = ("id,doi,display_name,publication_year,cited_by_count,type,"
          "primary_location,authorships,abstract_inverted_index,concepts,"
          "referenced_works_count")


def fetch_pages(pages: int, per_page: int, log=print):
    import urllib.error
    import urllib.parse
    import urllib.request

    from app.openalex import BASE, MAILTO, UA, with_key

    # 用 page 而不是 cursor：实测无 API key 时 **cursor 翻到第 2 页会稳定 401**
    # （同样的 page=2/5/20 都是 200）。这是踩出来的，不是文档里写的。
    for p in range(1, pages + 1):
        params = {
            "filter": "type:article,has_abstract:true",
            "sort": "cited_by_count:desc",
            "per_page": per_page,
            "page": p,
            "select": SELECT,
            "mailto": MAILTO,
        }
        url = with_key(f"{BASE}/works?{urllib.parse.urlencode(params)}")
        data = None
        for attempt in range(5):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    from app.openalex import disable_key
                    disable_key("403"); continue
                if exc.code in (401, 429, 500, 502, 503, 504) and attempt < 4:
                    wait = 3 * (attempt + 1)
                    log(f"    HTTP {exc.code}，{wait}s 后重试（{attempt+1}/4）")
                    time.sleep(wait)
                    continue
                # 单页彻底失败不拖垮整轮：已采数据保留，下次续采
                log(f"    放弃第 {p} 页（HTTP {exc.code}）")
                return
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    time.sleep(2 * (attempt + 1))
                    continue
                log(f"    放弃第 {p} 页（{type(exc).__name__}）")
                return
        if data is None:
            return
        results = data.get("results") or []
        log(f"  第 {p}/{pages} 页: {len(results)} 篇")
        yield from results
        if not results:
            return
        time.sleep(0.6)      # 页间节流：无 key 的共享预算对突发敏感


def run(pages: int = PAGES, log=print) -> dict:
    init_db()
    conn = connect()
    t0 = time.time()
    n_works = n_people_new = 0
    people_cites: collections.Counter[int] = collections.Counter()
    people_topics: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    people_orcid: dict[int, str | None] = {}
    people_inst: dict[int, str | None] = {}
    author_of: dict[str, int] = {}

    def get_or_make_person(author: dict, inst_name: str | None) -> int | None:
        nonlocal n_people_new
        oid = (author.get("id") or "").split("/")[-1]
        if not oid:
            return None
        if oid in author_of:
            return author_of[oid]
        row = conn.execute("SELECT pid FROM people WHERE openalex_id=?", (oid,)).fetchone()
        if row:
            author_of[oid] = int(row["pid"])
            return author_of[oid]
        cur = conn.execute(
            """INSERT INTO people(openalex_id,name,orcid,last_inst,status,updated_at)
               VALUES(?,?,?,?,'pending',?)""",
            (oid, (author.get("display_name") or "").strip(),
             author.get("orcid"), inst_name, time.time()))
        pid = int(cur.lastrowid)
        author_of[oid] = pid
        people_orcid[pid] = author.get("orcid")
        people_inst[pid] = inst_name
        n_people_new += 1
        return pid

    for work in fetch_pages(pages, PER_PAGE, log=log):
        wid_open = (work.get("id") or "").split("/")[-1]
        if not wid_open:
            continue
        loc = work.get("primary_location") or {}
        venue = ((loc.get("source") or {}) or {}).get("display_name")
        cur = conn.execute(
            """INSERT INTO works(openalex_id,doi,title,abstract,year,cites,venue,type)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(openalex_id) DO UPDATE SET cites=excluded.cites""",
            (wid_open, work.get("doi"), work.get("display_name") or "",
             abstract_from_inverted(work.get("abstract_inverted_index")),
             work.get("publication_year"), work.get("cited_by_count") or 0,
             venue, work.get("type")))
        # 注意：UPSERT 走"更新"分支时 lastrowid 不会刷新（会返回上一条 INSERT 的 rowid，
        # 很可能是 people.pid）。必须显式回查 wid，否则作者会被挂到错误的论文上。
        wid = int(conn.execute("SELECT wid FROM works WHERE openalex_id=?",
                               (wid_open,)).fetchone()["wid"])
        n_works += 1
        cites = work.get("cited_by_count") or 0
        concepts = [c.get("display_name") for c in (work.get("concepts") or [])[:5]
                    if c.get("display_name")]

        pids: list[int] = []
        for pos, au in enumerate(work.get("authorships") or []):
            author = au.get("author") or {}
            insts = au.get("institutions") or []
            inst_name = (insts[0].get("display_name") if insts else None)
            pid = get_or_make_person(author, inst_name)
            if pid is None:
                continue
            pids.append(pid)
            conn.execute("INSERT OR REPLACE INTO work_authors(wid,pid,position) VALUES(?,?,?)",
                         (wid, pid, pos))
            people_cites[pid] += cites
            for c in concepts:
                people_topics[pid][c] += 1

        # 合作边：只在同一篇论文内、且限制两两配对数（大团队 O(n²) 会爆）
        for i in range(min(len(pids), 12)):
            for j in range(i + 1, min(len(pids), 12)):
                a, b = sorted((pids[i], pids[j]))
                conn.execute("""INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',1)
                                ON CONFLICT(a,b,kind) DO UPDATE SET weight=weight+1""",
                             (a, b))
        if n_works % 1000 == 0:
            conn.commit()
            log(f"  已入库 {n_works} 篇 / 人 {len(author_of)} / {time.time()-t0:.0f}s")

    conn.commit()
    log("汇总人物画像 + 可信判定…")
    rows = conn.execute(
        """SELECT p.pid, p.openalex_id, p.name, p.orcid, p.last_inst,
                  COUNT(wa.wid) AS sample_works
           FROM people p LEFT JOIN work_authors wa ON wa.pid = p.pid
           GROUP BY p.pid""").fetchall()
    accepted = review = rejected = 0
    for r in rows:
        pid = int(r["pid"])
        topics = [t for t, _n in people_topics[pid].most_common(8)]
        feats = {
            "name": r["name"], "orcid": r["orcid"] or people_orcid.get(pid),
            "sample_works": r["sample_works"] or 0,
            "sample_cites": people_cites.get(pid, 0),
            "has_inst": r["last_inst"], "topics": topics,
        }
        judged = judge(feats)
        status = ("published" if judged["verdict"] == "accept"
                  else "pending" if judged["verdict"] == "review" else "rejected")
        conn.execute(
            """UPDATE people SET works_count=?, cited_by_count=?, topics=?, status=?,
                                 confidence=?, evidence=? WHERE pid=?""",
            (feats["sample_works"], feats["sample_cites"],
             json.dumps(topics, ensure_ascii=False), status, judged["score"],
             json.dumps({"source": f"https://api.openalex.org/authors/{r['openalex_id']}",
                         "features": feats, "reasons": judged["reasons"]},
                        ensure_ascii=False), pid))
        conn.execute(
            """INSERT INTO candidates(kind,payload,evidence,score,verdict,reasons,created_at)
               VALUES('person',?,?,?,?,?,?)""",
            (json.dumps({"pid": pid, "openalex_id": r["openalex_id"],
                         "name": r["name"]}, ensure_ascii=False),
             json.dumps({"source": f"https://api.openalex.org/authors/{r['openalex_id']}"}),
             judged["score"], judged["verdict"],
             json.dumps(judged["reasons"], ensure_ascii=False), time.time()))
        accepted += judged["verdict"] == "accept"
        review += judged["verdict"] == "review"
        rejected += judged["verdict"] == "reject"
        if judged["verdict"] == "review":
            conn.execute("INSERT OR REPLACE INTO review_queue(cid,priority) "
                         "VALUES(last_insert_rowid(), ?)", (judged["score"],))

    rebuild_fts(conn)
    set_meta(conn, "last_ingest_works", {"works": n_works, "people": len(author_of),
                                         "ts": time.time()})
    conn.commit()
    conn.close()
    return {"works": n_works, "people": len(author_of), "new_people": n_people_new,
            "accept": accepted, "review": review, "reject": rejected,
            "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    print(run(log=lambda s: print(s, flush=True)))
