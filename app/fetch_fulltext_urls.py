"""找出每篇论文的全文来源（优先 arXiv），存进 work_fulltext。

为什么必须走这一步：works 表只存了 DOI，没有 PDF 地址；
而 OpenAlex 的 `best_oa_location.pdf_url` 大量为 null（实测 Nature 那篇是 null，只有 landing page）。
`locations[]` 里才会出现 arxiv.org/abs/... 这类可直接下载的地址。

接口额度：8000 篇 / 200 每条 = 40 次请求。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import DB_PATH
from app.db import connect, init_db
from app.openalex import BASE, MAILTO, UA, with_key

DDL = """CREATE TABLE IF NOT EXISTS work_fulltext (
    wid INTEGER PRIMARY KEY,
    arxiv_id TEXT,
    pdf_url TEXT,
    source TEXT,
    fetched INTEGER DEFAULT 0,
    chars INTEGER DEFAULT 0
)"""

ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+/[0-9]{7})", re.I)
SELECT = "id,doi,locations"


def extract(wid: int, locs: list[dict]) -> tuple[str | None, str | None, str]:
    for loc in locs or []:
        for key in ("pdf_url", "landing_page_url"):
            url = loc.get(key) or ""
            m = ARXIV.search(url)
            if m:
                aid = m.group(1)
                return aid, f"https://arxiv.org/pdf/{aid}", "arxiv"
    # 退而求其次：任意 OA 的 pdf_url
    for loc in locs or []:
        if loc.get("is_oa") and loc.get("pdf_url"):
            return None, loc["pdf_url"], "oa_pdf"
    return None, None, "none"


def run(pages: int = 40, log=print) -> dict:
    init_db()
    conn = connect()
    conn.execute(DDL)
    conn.commit()
    stats = {"scanned": 0, "arxiv": 0, "oa_pdf": 0, "none": 0}
    for p in range(1, pages + 1):
        params = {"filter": "type:article,has_abstract:true", "sort": "cited_by_count:desc",
                  "per_page": 200, "page": p, "select": SELECT, "mailto": MAILTO}
        url = with_key(f"{BASE}/works?{urllib.parse.urlencode(params)}")
        data = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode())
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(4 * (attempt + 1))
                    continue
                log(f"    第 {p} 页失败 HTTP {exc.code}")
                break
            except (urllib.error.URLError, TimeoutError):
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
                    continue
                break
        if not data:
            continue
        for w in data.get("results") or []:
            oid = (w.get("id") or "").rsplit("/", 1)[-1]
            row = conn.execute("SELECT wid FROM works WHERE openalex_id=?", (oid,)).fetchone()
            if not row:
                continue
            aid, pdf, source = extract(int(row["wid"]), w.get("locations") or [])
            conn.execute("INSERT OR REPLACE INTO work_fulltext(wid,arxiv_id,pdf_url,source) "
                         "VALUES(?,?,?,?)", (int(row["wid"]), aid, pdf, source))
            stats["scanned"] += 1
            stats[source] = stats.get(source, 0) + 1
        conn.commit()
        if p % 10 == 0:
            log(f"  {p}/{pages} 页 {stats}")
        time.sleep(0.6)
    conn.close()
    log(f"  完成 {stats}")
    return stats


if __name__ == "__main__":
    print(run(log=lambda s: print(s, flush=True)))
