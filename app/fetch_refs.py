"""补引用关系（AMiner 的 `paper/relation` 对应物）。

现状：我们只有 `works.cites`（被引**计数**），没有"引用了谁"，所以做不了
引用链、"谁引用了这篇"、论文影响力路径。

做法：OpenAlex 的 `/works` 返回 `referenced_works`（该文引用的 openalex id 列表），
按 50 篇一批拉回，落成 `work_refs(wid, ref_openalex, ref_wid)`：
  - wid            本库论文
  - ref_openalex   被引论文的 openalex id（无论是否在库内）
  - ref_wid        若该被引论文也在本库，则填 wid，否则 NULL

成本：11,097 篇 / 50 = 223 次请求。

用法：
  python -m app.fetch_refs              # 补全部
  python -m app.fetch_refs --limit 500  # 试跑
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.db import connect, init_db
from app.openalex import BASE, MAILTO, UA, disable_key, with_key

BATCH = 50
SELECT = "id,referenced_works"

DDL = """
CREATE TABLE IF NOT EXISTS work_refs (
  wid           INTEGER NOT NULL,
  ref_openalex  TEXT    NOT NULL,
  ref_wid       INTEGER,
  PRIMARY KEY (wid, ref_openalex)
);
CREATE INDEX IF NOT EXISTS idx_work_refs_ref ON work_refs(ref_openalex);
"""


def ensure_schema() -> None:
    init_db()
    conn = connect()
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _fetch(ids: list[str], tries: int = 4) -> list[dict]:
    filt = "ids.openalex:" + "|".join(ids)
    params = {"filter": filt, "per_page": BATCH, "select": SELECT, "mailto": MAILTO}
    url = f"{BASE}/works?{urllib.parse.urlencode(params)}"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(with_key(url), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return (json.loads(r.read().decode()) or {}).get("results") or []
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                disable_key("403")
                continue
            if exc.code in (401, 429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            return []
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return []
    return []


def run(limit: int = 0, log=print) -> dict:
    ensure_schema()
    conn = connect()
    try:
        sql = ("SELECT wid, openalex_id FROM works "
               "WHERE openalex_id IS NOT NULL AND openalex_id != '' "
               "AND wid NOT IN (SELECT DISTINCT wid FROM work_refs) "
               "ORDER BY cites DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = [dict(r) for r in conn.execute(sql)]
        log(f"  待补引用: {len(rows)} 篇，预计 {len(rows)//BATCH + 1} 次请求")

        # 本库论文的 openalex_id → wid，用于把"被引论文"映射回库内
        in_corpus = {r["openalex_id"]: r["wid"]
                     for r in conn.execute("SELECT wid, openalex_id FROM works "
                                           "WHERE openalex_id IS NOT NULL")}
        t0 = time.time()
        total_refs = 0
        in_edges = 0
        for i in range(0, len(rows), BATCH):
            chunk = rows[i:i + BATCH]
            got = _fetch([r["openalex_id"] for r in chunk])
            by_id = {a.get("id", "").rsplit("/", 1)[-1]: a for a in got}
            payload = []
            for r in chunk:
                a = by_id.get(r["openalex_id"])
                if not a:
                    continue
                for ref in a.get("referenced_works") or []:
                    ref_id = str(ref).rsplit("/", 1)[-1]
                    ref_wid = in_corpus.get(ref_id)
                    payload.append((r["wid"], ref_id, ref_wid))
                    total_refs += 1
                    if ref_wid:
                        in_edges += 1
            if payload:
                conn.executemany(
                    "INSERT OR IGNORE INTO work_refs(wid, ref_openalex, ref_wid) VALUES(?,?,?)",
                    payload)
                conn.commit()
            if (i // BATCH) % 10 == 0:
                log(f"    {i + len(chunk)}/{len(rows)}  引用 {total_refs}（库内 {in_edges}）")
            time.sleep(0.2)

        stats = {
            "works": len(rows), "refs": total_refs, "in_corpus_edges": in_edges,
            "seconds": round(time.time() - t0, 1),
            "works_with_refs": conn.execute(
                "SELECT COUNT(DISTINCT wid) FROM work_refs").fetchone()[0],
            "corpus_link_rate": round(in_edges / max(total_refs, 1), 4),
        }
        conn.execute(
            "INSERT INTO meta(k, v) VALUES('work_refs', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(stats, ensure_ascii=False),))
        conn.commit()
        log(f"  完成：{stats['works']} 篇 / 引用 {total_refs} 条 / 库内边 {in_edges} "
            f"（{stats['corpus_link_rate']*100:.1f}%）/ {stats['seconds']}s")
        return stats
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    run(limit=a.limit)


if __name__ == "__main__":
    main()
