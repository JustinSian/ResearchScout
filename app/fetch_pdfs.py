"""下载全文 PDF 并抽文本（PyMuPDF）。

只下 work_fulltext 里 source ∈ {arxiv, oa_pdf} 的论文；校验 %PDF 魔数，
避免把出版商的 HTML 拦截页当正文（实测 nature.com/*.pdf 返回的是 HTML）。

用法：
  python -m app.fetch_pdfs --limit 40        # 试跑
  python -m app.fetch_pdfs --limit 2500 --workers 4
"""
from __future__ import annotations

import argparse
import io
import time
import urllib.error
import urllib.request

from app.db import connect, init_db
from app.openalex import UA

DDL = """CREATE TABLE IF NOT EXISTS work_text (
    wid INTEGER PRIMARY KEY,
    chars INTEGER NOT NULL,
    text TEXT NOT NULL,
    fetched_at REAL NOT NULL
)"""


def _download(url: str, timeout: int = 40, max_bytes: int = 25_000_000) -> bytes | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA + " (scholartrust fulltext fetch)",
        "Accept": "application/pdf,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read(max_bytes)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    if not blob.startswith(b"%PDF"):
        return None
    return blob


def _extract(blob: bytes) -> str:
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=blob, filetype="pdf")
    except Exception:
        return ""
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
        if sum(len(p) for p in parts) > 400_000:
            break
    doc.close()
    return "\n".join(parts)


def run(limit: int = 40, workers: int = 4, log=print) -> dict:
    init_db()
    conn = connect()
    conn.execute(DDL)
    conn.commit()
    todo = conn.execute(
        """SELECT f.wid, f.pdf_url FROM work_fulltext f
           LEFT JOIN work_text t ON t.wid = f.wid
           WHERE f.pdf_url IS NOT NULL AND t.wid IS NULL
           ORDER BY f.source='arxiv' DESC, f.wid LIMIT ?""", (limit,)).fetchall()
    conn.close()
    log(f"  待下载 {len(todo)} 篇")

    stats = {"ok": 0, "fail": 0, "empty": 0}
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor

    def work(item):
        wid, url = int(item["wid"]), item["pdf_url"]
        blob = _download(url)
        if blob is None:
            return wid, None, "fail"
        text = _extract(blob)
        if len(text.strip()) < 500:
            return wid, None, "empty"
        return wid, text, "ok"

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (wid, text, status) in enumerate(ex.map(work, todo), 1):
            stats[status] += 1
            if text:
                c = connect()
                c.execute("INSERT OR REPLACE INTO work_text(wid,chars,text,fetched_at) "
                          "VALUES(?,?,?,?)", (wid, len(text), text, time.time()))
                c.commit()
                c.close()
            if i % 20 == 0:
                log(f"    {i}/{len(todo)} ok={stats['ok']} fail={stats['fail']} "
                    f"empty={stats['empty']} {time.time()-t0:.0f}s")
    log(f"  完成 {stats} {time.time()-t0:.0f}s")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    print(run(a.limit, a.workers, log=lambda s: print(s, flush=True)))
