"""用 OpenAlex 补齐外部导入论文的真实作者，并重建"以某人为中心"的合作边。

为什么必须做：
1. 导入某学术图谱记录时只把他本人设成作者，7 篇论文变成"单作者论文"——数据是假的；
2. 主库建边时只连"前 12 位作者"，他的 COVID 论文有 35~44 位作者，
   他不在前 12，于是他的合作者只有 11 人，而论文里实际出现 70 人。

做法：
- 按 DOI 从 OpenAlex 取 authorships（每篇 1 次请求）；
- 补人、补 work_authors；
- **重建中心边**：中心人物与同篇的每位作者各连一条，权重=共同论文数。
  这避免了"全对全"的 O(n²) 爆炸（44 作者 = 946 条边/篇）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.db import connect, init_db
from app.judge import judge
from app.openalex import BASE, MAILTO, UA


def _fetch_doi(doi: str) -> dict | None:
    url = f"{BASE}/works/doi:{urllib.parse.quote(doi)}?select=id,authorships&mailto={MAILTO}"
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 429, 502, 503) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            return None
    return None


def enrich_work(conn, wid: int, doi: str, log=print) -> int:
    data = _fetch_doi(doi)
    if not data:
        log(f"    {wid}: 取不到 {doi}")
        return 0
    added = 0
    for au in data.get("authorships") or []:
        a = au.get("author") or {}
        oid = (a.get("id") or "").rsplit("/", 1)[-1]
        name = (a.get("display_name") or "").strip()
        if not oid or not name:
            continue
        row = conn.execute("SELECT pid FROM people WHERE openalex_id=?", (oid,)).fetchone()
        if row:
            pid = int(row["pid"])
        else:
            insts = au.get("institutions") or []
            inst = (insts[0].get("display_name") if insts else None) or None
            judged = judge({"name": name, "orcid": a.get("orcid"),
                            "sample_works": 1, "sample_cites": 0,
                            "cite_p50": 5504, "cite_p90": 18368,
                            "has_inst": inst, "topics": []})
            status = ("published" if judged["verdict"] == "accept"
                      else "pending" if judged["verdict"] == "review" else "rejected")
            cur = conn.execute(
                """INSERT INTO people(openalex_id,name,orcid,last_inst,works_count,
                       cited_by_count,status,confidence,topics,source,updated_at)
                   VALUES(?,?,?,?,1,0,?,?,'[]','openalex',?)""",
                (oid, name, a.get("orcid"), inst, status, judged["score"], time.time()))
            pid = int(cur.lastrowid)
            conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                         (pid, name, "", inst or ""))
            added += 1
        conn.execute("INSERT OR IGNORE INTO work_authors(wid,pid) VALUES(?,?)", (wid, pid))
    return added


def rebuild_center_edges(pid: int, wids: list[int], log=print) -> dict:
    """把"中心人物 ↔ 同篇作者"写成边（权重=共同论文数），替代原来的全对全。"""
    conn = connect()
    widset = set(wids)
    shared: dict[int, int] = {}
    for wid in widset:
        for r in conn.execute("SELECT pid FROM work_authors WHERE wid=? AND pid<>?",
                              (wid, pid)):
            other = int(r["pid"])
            shared[other] = shared.get(other, 0) + 1
    created = updated = 0
    for other, w in shared.items():
        a, b = sorted((pid, other))
        old = conn.execute("SELECT weight FROM edges WHERE a=? AND b=? AND kind='coauthor'",
                           (a, b)).fetchone()
        if old:
            conn.execute("UPDATE edges SET weight=? WHERE a=? AND b=? AND kind='coauthor'",
                         (max(old["weight"] or 0, w), a, b))
            updated += 1
        else:
            conn.execute("INSERT INTO edges(a,b,kind,weight) VALUES(?,?,'coauthor',?)",
                         (a, b, w))
            created += 1
    conn.commit()
    conn.close()
    log(f"  中心边：新建 {created}，更新 {updated}，合作者 {len(shared)} 人")
    return {"created": created, "updated": updated, "collaborators": len(shared)}


def run(pid: int, log=print) -> dict:
    init_db()
    conn = connect()
    works = conn.execute(
        """SELECT w.wid, w.doi FROM works w JOIN work_authors wa ON wa.wid=w.wid
           WHERE wa.pid=? ORDER BY w.year DESC""", (pid,)).fetchall()
    log(f"  他的论文 {len(works)} 篇，按 DOI 补作者")
    added = 0
    for w in works:
        if not w["doi"]:
            continue
        n = enrich_work(conn, int(w["wid"]), w["doi"], log=log)
        added += n
        time.sleep(0.4)
    conn.commit()
    conn.close()
    log(f"  新补作者 {added} 人")
    wids = [int(w["wid"]) for w in works]
    stats = rebuild_center_edges(pid, wids, log=log)
    return {"works": len(works), "new_people": added, **stats}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    a = ap.parse_args()
    print(json.dumps(run(a.pid), ensure_ascii=False))
