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
SELECT = "id,display_name,last_known_institutions,affiliations"


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


def _country_of(author: dict) -> tuple[str | None, str | None]:
    """国家 + 来源。返回 (ISO2, src)，src ∈ {author, affiliation, None}。

    两条来源的可信度不同，必须分开记：

    - `author`：`last_known_institutions[].country_code`，与库里存的 last_inst 同源；
    - `affiliation`：历史任职记录。**只有带 years 才敢用**——踩过的坑：
      某些记录的 years 缺失，取第一条就相当于随机挑一段几十年前的经历，
      结果把"曼彻斯特大学"的人标成了 CN。宁可留空，也不要这种数据。
    """
    for inst in author.get("last_known_institutions") or []:
        cc = (inst or {}).get("country_code")
        if cc:
            return str(cc).upper(), "author"

    best_cc, best_year = None, -1
    for aff in author.get("affiliations") or []:
        inst = (aff or {}).get("institution") or {}
        cc = inst.get("country_code")
        years = [y for y in ((aff or {}).get("years") or []) if isinstance(y, int)]
        if not cc or not years:
            continue          # 没有年份 = 不知道是不是"最近"的，直接跳过
        y = max(years)
        if y > best_year:
            best_cc, best_year = str(cc).upper(), y
    if best_cc:
        return best_cc, "affiliation"
    return None, None


def ensure_schema() -> None:
    """加 country_src：不记来源就分不清"作者自报"和"历史任职推断"。"""
    conn = connect()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(people)")}
        if "country_src" not in cols:
            conn.execute("ALTER TABLE people ADD COLUMN country_src TEXT")
            conn.commit()
    finally:
        conn.close()


def infer_from_institution(min_share: float = 0.8, log=print) -> dict:
    """兜底：用"机构的多数国家"推断仍未补上的人。

    只统计 **src='author'** 的人来确定机构国家：拿历史任职推出来的国家再去推别人，
    会把一次错误放大成一片。混合国籍机构（CERN/WHO）在阈值下自然不会命中。
    """
    init_db()
    ensure_schema()
    conn = connect()
    try:
        rows = conn.execute("""
            SELECT p.pid, i.country,
                   (SELECT COUNT(*) FROM people q WHERE q.inst_id = p.inst_id
                      AND q.country_src = 'author') AS auth_n,
                   (SELECT COUNT(*) FROM people q WHERE q.inst_id = p.inst_id
                      AND q.country_src = 'author' AND q.country = i.country) AS auth_same
            FROM people p JOIN institutions i ON i.iid = p.inst_id
            WHERE (p.country IS NULL OR p.country = '')
              AND i.country IS NOT NULL AND i.country != ''
        """).fetchall()
        updates = []
        for r in rows:
            auth_n, same = int(r["auth_n"] or 0), int(r["auth_same"] or 0)
            if auth_n >= 3 and same / auth_n >= min_share:
                updates.append((r["country"], r["pid"]))
        if updates:
            conn.executemany(
                "UPDATE people SET country=?, country_src='institution' WHERE pid=?", updates)
            conn.commit()
        left = conn.execute(
            "SELECT COUNT(*) FROM people WHERE country IS NULL OR country = ''").fetchone()[0]
        log(f"  机构推断：候选 {len(rows)}，补上 {len(updates)}，仍缺 {left}")
        return {"candidates": len(rows), "filled": len(updates), "left": left}
    finally:
        conn.close()


def run(scope: str = "published", limit: int = 0, log=print) -> dict:
    init_db()
    ensure_schema()
    conn = connect()
    # 用 country_src 判断"补过没有"：老数据 country 有值但来源不明，也要重算一遍
    where = "country_src IS NULL AND openalex_id IS NOT NULL AND openalex_id != ''"
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
            cc, src = _country_of(a) if a else (None, None)
            if cc:
                updates.append((cc, src, r["pid"]))
            else:
                # 取不到也要落 src='none'，否则下一轮还会再抓一次
                updates.append((None, "none", r["pid"]))
                missing += 1
        if updates:
            conn.executemany(
                "UPDATE people SET country=?, country_src=? WHERE pid=?", updates)
            conn.commit()
            filled += sum(1 for u in updates if u[0])
        if (i // BATCH) % 10 == 0:
            log(f"    {i + len(chunk)}/{len(rows)} 已补 {filled}")
        time.sleep(0.2)

    # 机构国家：只按"作者自报"的人推断（不能用历史任职推出来的国家再往下推）
    # 先把旧值清空——之前那轮用的是"所有人"的多数，掺了推断出来的国家，要重算
    conn.execute("UPDATE institutions SET country = NULL")
    conn.execute("""UPDATE institutions SET country = (
                        SELECT p.country FROM people p
                        WHERE p.inst_id = institutions.iid AND p.country_src = 'author'
                        GROUP BY p.country ORDER BY COUNT(*) DESC LIMIT 1)
                    WHERE country IS NULL OR country = ''""")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM people WHERE country IS NOT NULL").fetchone()[0]
    by_src = {r[0]: r[1] for r in conn.execute(
        "SELECT country_src, COUNT(*) FROM people WHERE country IS NOT NULL GROUP BY country_src")}
    inst_c = conn.execute("SELECT COUNT(*) FROM institutions WHERE country IS NOT NULL AND country != ''").fetchone()[0]
    conn.close()
    stats = {"filled": filled, "missing": missing, "people_total": total,
             "by_src": by_src,
             "institutions_with_country": inst_c, "seconds": round(time.time() - t0, 1)}
    log(f"  完成：补上 {filled}，未取到 {missing}；全库有国家 {total} 人"
        f"（来源 {by_src}）；机构有国家 {inst_c} 个；耗时 {stats['seconds']}s")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="补全库（默认只补已发布）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--infer", action="store_true", help="用机构多数国家兜底推断")
    a = ap.parse_args()
    if a.infer:
        infer_from_institution()
    else:
        run(scope="all" if a.all else "published", limit=a.limit)
        infer_from_institution()


if __name__ == "__main__":
    main()
