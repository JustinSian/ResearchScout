"""专家深档案（dossier）：每人**全量成果** + 派生指标。

为什么需要：现在库里每人平均 1.7 篇论文，"专家"只是个人名。
尽调 Agent 要想说出"代表作/主题迁移/合作圈/异常年份"，必须先有完整成果清单。

数据模型：
  dossier(pid, author_id, fetched_at, works_total, works_loaded, metrics_json)
  dossier_work(pid, wid, year, cites, venue, doi, title, is_rep)

成本：每位专家按作品数 / 200 翻页。100 位专家 ≈ 300~1000 次请求
（有 API key 时 $1/天 = 1 万次，够用；无 key 时 1000 次/天，需分批）。

用法：
  python -m app.build_dossiers --plan            # 只打印选谁、预计多少请求
  python -m app.build_dossiers --limit 2         # 试点 2 人
  python -m app.build_dossiers --limit 100       # 全量
  python -m app.build_dossiers --limit 100 --force   # 重拉（忽略缓存）
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

from app.db import connect, init_db
from app.openalex import BASE, MAILTO, UA, disable_key, with_key

DDL = [
    """CREATE TABLE IF NOT EXISTS dossier (
        pid INTEGER PRIMARY KEY,
        author_id TEXT,
        fetched_at REAL,
        works_total INTEGER,
        works_loaded INTEGER,
        metrics_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS dossier_work (
        pid INTEGER NOT NULL,
        wid INTEGER,
        title TEXT,
        year INTEGER,
        cites INTEGER,
        venue TEXT,
        doi TEXT,
        is_rep INTEGER DEFAULT 0,
        PRIMARY KEY (pid, wid)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_dw ON dossier_work(pid)",
]

SELECT = ("id,doi,title,publication_year,cited_by_count,type,"
          "primary_location,authorships,concepts")


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
                time.sleep(4 * (attempt + 1)); continue
            return None
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1)); continue
            return None
    return None


def _h_index(cites: list[int]) -> int:
    c = sorted(cites, reverse=True)
    h = 0
    for i, v in enumerate(c, 1):
        if v >= i:
            h = i
        else:
            break
    return h


def build_one(conn, pid: int, author_id: str, max_pages: int = 12, log=print) -> dict:
    rows: list[dict] = []
    pages = 0
    total = None
    while pages < max_pages:
        params = {"filter": f"authorships.author.id:{author_id}", "per_page": 200,
                  "page": pages + 1, "select": SELECT, "mailto": MAILTO}
        data = _get(f"{BASE}/works?{urllib.parse.urlencode(params)}")
        if not data:
            log(f"    {pid}: 第 {pages+1} 页失败，停止")
            break
        pages += 1
        total = (data.get("meta") or {}).get("count") or total
        res = data.get("results") or []
        for w in res:
            loc = w.get("primary_location") or {}
            rows.append({
                "wid": (w.get("id") or "").split("/")[-1],
                "title": w.get("title") or "",
                "year": w.get("publication_year"),
                "cites": w.get("cited_by_count") or 0,
                "venue": ((loc.get("source") or {}) or {}).get("display_name"),
                "doi": w.get("doi"),
            })
        if len(res) < 200:
            break
        time.sleep(0.5)

    # 代表作：被引 top 10（且年份合法）
    ranked = sorted([r for r in rows if r["year"]], key=lambda r: -r["cites"])[:10]
    rep = {r["wid"] for r in ranked}
    conn.execute("DELETE FROM dossier_work WHERE pid=?", (pid,))
    conn.executemany(
        """INSERT OR REPLACE INTO dossier_work(pid,wid,title,year,cites,venue,doi,is_rep)
           VALUES(?,?,?,?,?,?,?,?)""",
        [(pid, int(r["wid"].replace("W", "")) if r["wid"].startswith("W") else hash(r["wid"]),
          r["title"], r["year"], r["cites"], r["venue"], r["doi"], 1 if r["wid"] in rep else 0)
         for r in rows])

    cites = [r["cites"] for r in rows]
    years = [r["year"] for r in rows if r["year"]]
    venues = collections = {}
    for r in rows:
        if r["venue"]:
            venues[r["venue"]] = venues.get(r["venue"], 0) + 1
    metrics = {
        "works": len(rows),
        "works_total_openalex": total,
        "cites_sum": sum(cites),
        "cites_median": statistics.median(cites) if cites else 0,
        "h_index": _h_index(cites),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "recent_5y_share": round(sum(1 for y in years if y >= (max(years) - 4)) / max(len(years), 1), 3) if years else 0,
        "top_venues": sorted(venues.items(), key=lambda kv: -kv[1])[:5],
        "rep_papers": [{"title": r["title"][:80], "year": r["year"], "cites": r["cites"]}
                       for r in ranked[:5]],
    }
    conn.execute(
        """INSERT OR REPLACE INTO dossier(pid,author_id,fetched_at,works_total,works_loaded,metrics_json)
           VALUES(?,?,?,?,?,?)""",
        (pid, author_id, time.time(), total or len(rows), len(rows),
         json.dumps(metrics, ensure_ascii=False)))
    conn.commit()
    # 记忆：与上次快照比对，记录"这位专家变了什么"
    try:
        from app import memory
        memory.diff_and_store(pid, metrics)
    except Exception as exc:
        print(f"    [memory] diff 失败: {type(exc).__name__}: {exc}")
    return {"pid": pid, "works": len(rows), "pages": pages, "h_index": metrics["h_index"]}


def pick_experts(conn, n: int) -> list[tuple[int, str]]:
    rows = conn.execute(
        """SELECT pid, openalex_id FROM people
           WHERE status='published' AND openalex_id NOT NULL
             AND openalex_id NOT LIKE 'ext:%' AND cited_by_count > 0
           ORDER BY cited_by_count DESC LIMIT ?""", (n,)).fetchall()
    return [(int(r["pid"]), r["openalex_id"]) for r in rows]


def pick_from_network(conn, seeds: list[int], n: int) -> list[tuple[int, str]]:
    """从若干种子专家的合作网络里选人：先放种子本人，再按共同论文数排序。"""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for s in seeds:
        row = conn.execute("SELECT pid, openalex_id FROM people WHERE pid=?", (s,)).fetchone()
        if row and row["openalex_id"]:
            out.append((int(row["pid"]), row["openalex_id"]))
            seen.add(int(row["pid"]))
    ph = ",".join("?" * len(seeds))
    rows = conn.execute(
        f"""SELECT p.pid, p.openalex_id, MAX(e.weight) AS w, p.cited_by_count
            FROM people p
            JOIN edges e ON (e.kind='coauthor' AND (e.a=p.pid OR e.b=p.pid))
            WHERE (e.a IN ({ph}) OR e.b IN ({ph}))
              AND p.status='published' AND p.openalex_id IS NOT NULL
              AND p.openalex_id NOT LIKE 'ext:%' AND p.pid NOT IN ({ph})
            GROUP BY p.pid
            ORDER BY w DESC, p.cited_by_count DESC
            LIMIT ?""",
        (*seeds, *seeds, *seeds, n)).fetchall()
    for r in rows:
        pid = int(r["pid"])
        if pid in seen:
            continue
        out.append((pid, r["openalex_id"]))
        seen.add(pid)
    return out[:n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--network", default="", help="种子 pid，逗号分隔（如 527,503,498）")
    a = ap.parse_args()
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    target = a.limit or 100
    if a.network:
        seeds = [int(x) for x in a.network.split(",") if x.strip()]
        experts = pick_from_network(conn, seeds, target)
        print(f"  来源：{len(seeds)} 个种子的合作网络")
    else:
        experts = pick_experts(conn, target)
    done = {int(r["pid"]) for r in conn.execute("SELECT pid FROM dossier")}
    todo = [(p, a_) for p, a_ in experts if a_.split("/")[-1] and (a.force or p not in done)]
    est = sum(1 for _ in todo) * 3
    print(f"  目标 {len(experts)} 人；已建档 {len(done)}；本次待建 {len(todo)}；预计 ≈{est} 次请求")
    if a.plan:
        for p, a_ in todo[:10]:
            nm = conn.execute("SELECT name,works_count FROM people WHERE pid=?", (p,)).fetchone()
            print(f"    pid={p:<7} {nm['name'][:30]:<32} OpenAlex 作品 {nm['works_count']}")
        return
    for i, (pid, aid) in enumerate(todo, 1):
        res = build_one(conn, pid, aid.split("/")[-1])
        print(f"  [{i}/{len(todo)}] pid={pid} → {res['works']} 篇（{res['pages']} 页，h={res['h_index']}）")
    conn.close()


if __name__ == "__main__":
    main()
