"""用全文重建 chunk 索引（有全文用全文，没有就退回摘要）。

对比实验的关键：同一批 8,000 篇里，2,400 篇有全文。所以评测时要同时看
"全库口径"和"有全文子集口径"——后者才能回答"全文到底有没有用"。
"""
from __future__ import annotations

import time

from app.chunking import split_abstract
from app.chunking_full import split_fulltext
from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS chunks (
        cid INTEGER PRIMARY KEY, wid INTEGER NOT NULL, ord INTEGER NOT NULL,
        text TEXT NOT NULL, chars INTEGER NOT NULL, src TEXT DEFAULT 'abstract')""",
    "CREATE INDEX IF NOT EXISTS idx_chunks_wid ON chunks(wid)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        text, tokenize='porter unicode61')""",
]


def run(log=print) -> dict:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    # 老表没有 src 列时补上
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chunks)")]
    if "src" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN src TEXT DEFAULT 'abstract'")
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("DELETE FROM vectors WHERE kind='chunks'")

    full = {int(r["wid"]): r["text"]
            for r in conn.execute("SELECT wid, text FROM work_text WHERE chars > 500")}
    rows = conn.execute("SELECT wid, abstract FROM works").fetchall()
    t0 = time.time()
    n = n_full = 0
    for r in rows:
        wid = int(r["wid"])
        if wid in full:
            parts, src = split_fulltext(full[wid]), "fulltext"
            n_full += 1
        else:
            parts, src = split_abstract(r["abstract"] or ""), "abstract"
        for i, txt in enumerate(parts):
            cur = conn.execute(
                "INSERT INTO chunks(wid,ord,text,chars,src) VALUES(?,?,?,?,?)",
                (wid, i, txt, len(txt), src))
            conn.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)",
                         (cur.lastrowid, txt))
            n += 1
        if n % 8000 < len(parts):
            conn.commit()
    conn.commit()
    per = n / max(len(rows), 1)
    log(f"  chunks {n}（全文 {n_full} 篇 / 摘要 {len(rows)-n_full} 篇，"
        f"平均 {per:.1f} 块/篇，{time.time()-t0:.0f}s）")
    log("  分布: " + str({r["src"]: r["n"] for r in conn.execute(
        "SELECT src, COUNT(*) n FROM chunks GROUP BY src")}))
    conn.close()
    return {"chunks": n, "fulltext_works": n_full, "chunks_per_work": round(per, 2)}


if __name__ == "__main__":
    print(run(log=lambda s: print(s, flush=True)))
