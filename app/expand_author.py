"""以某位专家为种子，按图谱关系扩张专家库。

流程（每一步都可单独重跑）：
  1. 按 OpenAlex author id 拉他的**全部论文**（cursor 翻页，200 篇/页）
  2. 论文入库 → 作者入库 → 建立"中心 ↔ 每位合作者"的星形边（权重=共同论文数）
  3. 批量给合作者补档案（works_count / cited / 机构 / ORCID / 主题）
  4. 重跑可信判定：合作者若 ≥10 篇作品，会拿到"网络背书"这条证据
  5. 输出：把"合作强度前 N 位的已发布专家"顶到专家墙

为什么用星形边而不是全对全：他的论文里有 100 位作者的大协作论文，
全对全是 4,950 条边/篇，既慢又没有意义。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.db import connect, init_db
from app.judge import judge
from app.openalex import BASE, MAILTO, UA, disable_key, with_key

WORK_SELECT = ("id,doi,display_name,publication_year,cited_by_count,type,"
               "primary_location,authorships,concepts")
AUTHOR_SELECT = ("id,display_name,orcid,works_count,cited_by_count,"
                 "last_known_institutions,x_concepts")


def _get(url: str, tries: int = 4) -> dict | None:
    for attempt in range(tries):
        try:
            req = urllib.request.Request(with_key(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                disable_key("403"); continue
            if exc.code in (401, 429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(4 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            return None
    return None


def _upsert_work(conn, w: dict) -> int | None:
    oid = (w.get("id") or "").rsplit("/", 1)[-1]
    if not oid:
        return None
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    conn.execute(
        """INSERT INTO works(openalex_id,doi,title,abstract,year,cites,venue,type)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(openalex_id) DO UPDATE SET
             cites=excluded.cites, venue=excluded.venue, title=excluded.title""",
        (oid, w.get("doi"), w.get("display_name") or "", None,
         w.get("publication_year"), w.get("cited_by_count") or 0, venue, w.get("type")))
    # 必须回查，不能用 lastrowid（UPSERT 更新分支不刷新它）
    row = conn.execute("SELECT wid FROM works WHERE openalex_id=?", (oid,)).fetchone()
    return int(row["wid"]) if row else None


def _upsert_person(conn, author: dict, inst: str | None) -> int | None:
    aid = (author.get("id") or "").rsplit("/", 1)[-1]
    if not aid:
        return None
    row = conn.execute("SELECT pid FROM people WHERE openalex_id=?", (aid,)).fetchone()
    if row:
        return int(row["pid"])
    cur = conn.execute(
        """INSERT INTO people(openalex_id,name,orcid,last_inst,status,updated_at)
           VALUES(?,?,?,?,'pending',?)""",
        (aid, (author.get("display_name") or "").strip(), author.get("orcid"),
         inst, time.time()))
    return int(cur.lastrowid)


def expand(pid: int, author_id: str, max_works: int = 2000, log=print) -> dict:
    init_db()
    conn = connect()
    t0 = time.time()
    pages = 0
    new_works = new_people = linked = 0
    shared: dict[int, int] = {}

    while pages * 200 < max_works:
        params = {"filter": f"authorships.author.id:{author_id}", "per_page": 200,
                  "page": pages + 1, "select": WORK_SELECT, "mailto": MAILTO}
        data = _get(f"{BASE}/works?{urllib.parse.urlencode(params)}")
        if not data:
            log(f"  第 {pages+1} 页取数失败，停止")
            break
        pages += 1
        for w in data.get("results") or []:
            wid = _upsert_work(conn, w)
            if not wid:
                continue
            new_works += 1
            for au in w.get("authorships") or []:
                a = au.get("author") or {}
                insts = au.get("institutions") or []
                inst = (insts[0].get("display_name") if insts else None) or None
                opid = _upsert_person(conn, a, inst)
                if not opid:
                    continue
                if opid != pid:
                    new_people += 1
                conn.execute("INSERT OR IGNORE INTO work_authors(wid,pid) VALUES(?,?)",
                             (wid, opid))
                linked += 1
                if opid != pid:
                    shared[opid] = shared.get(opid, 0) + 1
        conn.commit()
        log(f"  第 {pages} 页：累计 {new_works} 篇 / 合作者 {len(shared)} 人 / {time.time()-t0:.0f}s")
        if not (data.get("results") or []):
            break
        time.sleep(0.6)

    # 星形边：中心 ↔ 每位合作者，权重=共同论文数
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_edges ON edges(a,b,kind)")
    created = 0
    for other, w in shared.items():
        a, b = sorted((pid, other))
        old = conn.execute("SELECT weight FROM edges WHERE a=? AND b=? AND kind='coauthor'",
                           (a, b)).fetchone()
        if old:
            conn.execute("UPDATE edges SET weight=? WHERE a=? AND b=? AND kind='coauthor'",
                         (max(old["weight"] or 0, w), a, b))
        else:
            conn.execute("INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',?)",
                         (a, b, w))
            created += 1
    conn.commit()
    # 中心人物的作品数也要更新
    n_center = conn.execute("SELECT COUNT(*) FROM work_authors WHERE pid=?", (pid,)).fetchone()[0]
    conn.execute("UPDATE people SET works_count=? WHERE pid=?", (n_center, pid))
    conn.commit()
    conn.close()
    log(f"  完成：{new_works} 篇 / 合作者 {len(shared)} 人 / 新建边 {created} 条")
    return {"pages": pages, "works": new_works, "collaborators": len(shared),
            "edges_created": created, "center_works": n_center,
            "seconds": round(time.time() - t0, 1)}


def enrich_collaborators(pid: int, max_enrich: int = 0, log=print) -> dict:
    """给网络内所有人补档案（一次 50 个），并按新证据重判。"""
    conn = connect()
    rows = conn.execute(
        """SELECT p.pid, p.openalex_id, e.weight AS w FROM edges e
           JOIN people p ON p.pid = CASE WHEN e.a=? THEN e.b ELSE e.a END
           WHERE (e.a=? OR e.b=?) AND e.kind='coauthor' AND p.openalex_id IS NOT NULL""",
        (pid, pid, pid)).fetchall()
    shared = {int(r["pid"]): int(r["w"]) for r in rows}
    rows = sorted(rows, key=lambda r: -int(r["w"]))   # 按合作强度排序
    if max_enrich:
        rows = rows[:max_enrich]
    ids = [r["openalex_id"].rsplit("/", 1)[-1] for r in rows]
    pid_of = {r["openalex_id"].rsplit("/", 1)[-1]: int(r["pid"]) for r in rows}
    log(f"  待补档案 {len(ids)} 人")
    updated = published = 0
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        filt = "ids.openalex:" + "|".join(batch)
        data = _get(f"{BASE}/authors?filter={urllib.parse.quote(filt)}&per_page=50"
                    f"&select={AUTHOR_SELECT}&mailto={MAILTO}")
        for a in (data or {}).get("results") or []:
            aid = (a.get("id") or "").rsplit("/", 1)[-1]
            p = pid_of.get(aid)
            if not p:
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
                     # 只共同署名过 1 篇（尤其是百人大论文）不算同行认可，
                     # 要求 ≥2 篇共同论文才给"网络背书"这条证据
                     "coauthor_of_verified": works >= 10 and shared.get(p, 0) >= 2}
            judged = judge(feats)
            status = ("published" if judged["verdict"] == "accept"
                      else "pending" if judged["verdict"] == "review" else "rejected")
            endorse = "network" if feats["coauthor_of_verified"] else None
            conn.execute(
                """UPDATE people SET name=?, orcid=COALESCE(?,orcid),
                       last_inst=CASE WHEN source='huafu' THEN last_inst ELSE COALESCE(?,last_inst) END, works_count=?, cited_by_count=?,
                       topics=?, status=?, confidence=?, evidence=?, endorse=?,
                       updated_at=? WHERE pid=?""",
                (a.get("display_name") or "", a.get("orcid"), inst, works, cites,
                 json.dumps(topics, ensure_ascii=False), status, judged["score"],
                 json.dumps({"features": feats, "reasons": judged["reasons"],
                             "source": f"network_of:{pid}"}, ensure_ascii=False),
                 endorse, time.time(), p))
            conn.execute("DELETE FROM people_fts WHERE rowid=?", (p,))
            conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                         (p, a.get("display_name") or "", " ".join(topics), inst or ""))
            updated += 1
            published += status == "published"
        conn.commit()
        time.sleep(0.4)
    conn.close()
    log(f"  更新 {updated} 人，已发布 {published}")
    return {"scanned": len(ids), "updated": updated, "published": published}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--openalex", required=True, help="如 A5101475024")
    ap.add_argument("--max-works", type=int, default=2000)
    ap.add_argument("--max-enrich", type=int, default=0, help="只补合作最紧密的前 N 人（省额度）")
    a = ap.parse_args()
    print(json.dumps(expand(a.pid, a.openalex, a.max_works), ensure_ascii=False))
    print(json.dumps(enrich_collaborators(a.pid, a.max_enrich), ensure_ascii=False))
