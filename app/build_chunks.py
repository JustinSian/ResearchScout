"""建 chunk 索引：works.abstract → 句块 → FTS5 + 向量。

用法：
  python -m app.build_chunks              # 只建 FTS（秒级）
  python -m app.build_chunks --vectors    # 同时建向量（3 万块约 15~20 分钟）
"""
from __future__ import annotations

import argparse
import time

from app.chunking import split_abstract
from app.db import connect, init_db

DDL = [
    """CREATE TABLE IF NOT EXISTS chunks (
        cid INTEGER PRIMARY KEY,
        wid INTEGER NOT NULL,
        ord INTEGER NOT NULL,
        text TEXT NOT NULL,
        chars INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chunks_wid ON chunks(wid)",
    # 独立表的 FTS5（用 rowid 对齐 cid），与 people_fts 同一套做法
    """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        text, tokenize='porter unicode61')""",
    """CREATE TABLE IF NOT EXISTS chunks_vec_meta (cid INTEGER PRIMARY KEY, built_at REAL)""",
]


def build_fts(log=print) -> dict:
    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM chunks_fts")
    rows = conn.execute("SELECT wid, abstract FROM works WHERE abstract IS NOT NULL").fetchall()
    t0 = time.time()
    n_chunks = 0
    for r in rows:
        parts = split_abstract(r["abstract"] or "")
        for i, txt in enumerate(parts):
            cur = conn.execute(
                "INSERT INTO chunks(wid,ord,text,chars) VALUES(?,?,?,?)",
                (r["wid"], i, txt, len(txt)))
            conn.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)",
                         (cur.lastrowid, txt))
            n_chunks += 1
        if n_chunks % 4000 < len(parts):
            conn.commit()
    conn.commit()
    per = n_chunks / max(len(rows), 1)
    log(f"  chunks {n_chunks}（{len(rows)} 篇，平均 {per:.1f} 块/篇，{time.time()-t0:.0f}s）")
    conn.close()
    return {"works": len(rows), "chunks": n_chunks, "chunks_per_work": round(per, 2)}


def build_vectors(batch: int = 64, log=print) -> dict:
    from app.embed import encode, to_blob

    init_db()
    conn = connect()
    for ddl in DDL:
        conn.execute(ddl)
    conn.commit()
    rows = conn.execute("SELECT cid, text FROM chunks ORDER BY cid").fetchall()
    log(f"  待编码 {len(rows)} 块")
    conn.execute("DELETE FROM vectors WHERE kind='chunks'")
    t0 = time.time()
    done = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        vecs = encode([r["text"] for r in chunk])
        conn.executemany(
            "INSERT OR REPLACE INTO vectors(kind,ref_id,dim,vec) VALUES('chunks',?,?,?)",
            [(r["cid"], int(v.shape[0]), to_blob(v)) for r, v in zip(chunk, vecs)])
        done += len(chunk)
        if done % 3200 == 0:
            conn.commit()
            log(f"    {done}/{len(rows)}  {time.time()-t0:.0f}s")
    conn.commit()
    conn.close()
    log(f"  向量完成 {len(rows)} 块 {time.time()-t0:.0f}s")
    return {"chunks": len(rows), "seconds": round(time.time() - t0, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", action="store_true")
    a = ap.parse_args()
    print(build_fts())
    if a.vectors:
        print(build_vectors())
