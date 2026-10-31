"""补国家字段：从 OpenAlex 作者记录取 `last_known_institutions[].country_code`。

为什么需要：AMiner 式助手的核心是"自然语言 → 结构化筛选"，而"国家"是最常用的维度之一。
我们的 `people.country` 列一直空着（采集时没存），这里按批补齐。

成本：50 人/次请求。8,490 位已发布专家 ≈ 170 次请求（有 key 时 1 万次/天，够用）。

用法：
  python -m app.enrich_country                 # 只补已发布的（默认）
  python -m app.enrich_country --all           # 补全库
  python -m app.enrich_country --limit 500     # 只补前 N 个（试跑）
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
SELECT = "id,display_name,last_known_institutions"


def _fetch(ids: list[str], tries: int = 4) -> list[dict]:
    filt = "ids.openalex:" + "|".join(ids)
    params = {"filter": filt, "per_page": BATCH, "select": SELECT, "mailto": MAILTO}
    url = f"{BASE}/authors?{urllib.parse.urlencode(params)}"
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


def _country_of(author: dict) -> str | None:
    """取第一个机构的 country_code；没有就返回 None（不猜）。"""
    insts = author.get("last_known_institutions") or []
    for inst in insts:
        cc = (inst or {}).get("country_code")
        if cc:
            return str(cc).upper()
    return None


def run(scope: str = "published", limit: int = 0, log=print) -> dict:
    init_db()
    conn = connect()
    where = "country IS NULL AND openalex_id IS NOT NULL AND openalex_id != ''"
    if scope == "published":
        where += " AND status='published'"
    sql = f"SELECT pid, openalex_id, name FROM people WHERE {where} ORDER BY cited_by_count DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in conn.execute(sql)]
    log(f"  待补国家: {len(rows)} 人（范围={scope}），预计 {len(rows)//BATCH + 1} 次请求")

    filled = 0
    missing = 0
    t0 = time.time()
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        ids = [r["openalex_id"] for r in chunk]
        results = _fetch(ids)
        by_id = {a.get("id", "").rsplit("/", 1)[-1]: a for a in results}
        updates = []
        for r in chunk:
            a = by_id.get(r["openalex_id"])
            cc = _country_of(a) if a else None
            if cc:
                updates.append((cc, r["pid"]))
            else:
                missing += 1
        if updates:
            conn.executemany("UPDATE people SET country=? WHERE pid=?", updates)
            conn.commit()
            filled += len(updates)
        if (i // BATCH) % 10 == 0:
            log(f"    {i + len(chunk)}/{len(rows)} 已补 {filled}")
        time.sleep(0.2)

    # 机构国家：按挂靠人的多数国家推断
    conn.execute("""UPDATE institutions SET country = (
                        SELECT p.country FROM people p
                        WHERE p.inst_id = institutions.iid AND p.country IS NOT NULL
                        GROUP BY p.country ORDER BY COUNT(*) DESC LIMIT 1)
                    WHERE country IS NULL OR country = ''""")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people WHERE country IS NOT NULL").fetchone()[0]
    inst_c = conn.execute("SELECT COUNT(*) FROM institutions WHERE country IS NOT NULL AND country != ''").fetchone()[0]
    conn.close()
    stats = {"filled": filled, "missing": missing, "people_total": total,
             "institutions_with_country": inst_c, "seconds": round(time.time() - t0, 1)}
    log(f"  完成：补上 {filled}，未取到 {missing}；全库有国家 {total} 人；"
        f"机构有国家 {inst_c} 个；耗时 {stats['seconds']}s")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="补全库（默认只补已发布）")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    run(scope="all" if a.all else "published", limit=a.limit)


if __name__ == "__main__":
    main()
