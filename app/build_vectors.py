"""建向量索引：人物画像 + 论文（标题+摘要）。

人物文档构造（决定语义检索质量的关键）：
  name + 机构 + top topics（最多 8 个）+ 代表作标题（最多 5 篇）
只放 name+topics 时，像"图神经网络"这种方向查询召回会很差——所以把代表作标题也拼进去。

用法：
  python -m app.build_vectors --kind people        # 36k 人，约 8 分钟
  python -m app.build_vectors --kind works         # 8k 篇
  python -m app.build_vectors --kind all
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

from app.db import connect, init_db
from app.embed import encode, to_blob

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    kind TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (kind, ref_id)
);
"""


def _person_docs(conn) -> list[tuple[int, str]]:
    rows = conn.execute("""
        SELECT p.pid, p.name, p.last_inst, p.topics
        FROM people p WHERE p.status='published'
    """).fetchall()
    out: list[tuple[int, str]] = []
    for r in rows:
        topics = " ".join(json.loads(r["topics"] or "[]")[:8])
        papers = conn.execute(
            """SELECT w.title FROM works w JOIN work_authors wa ON wa.wid=w.wid
               WHERE wa.pid=? ORDER BY w.cites DESC LIMIT 5""", (r["pid"],)).fetchall()
        titles = " ".join((p["title"] or "")[:120] for p in papers)
        doc = f"{r['name']}. {r['last_inst'] or ''}. {topics}. {titles}".strip()
        out.append((int(r["pid"]), doc))
    return out


def _work_docs(conn) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT wid, title, abstract, venue FROM works").fetchall()
    return [(int(r["wid"]),
             f"{r['title'] or ''}. {(r['abstract'] or '')[:600]}")
            for r in rows]


def build(kind: str = "people", batch: int = 64, log=print) -> dict:
    init_db()
    conn = connect()
    conn.execute(SCHEMA)
    conn.commit()
    kinds = ["people", "works"] if kind == "all" else [kind]
    stats = {}
    for k in kinds:
        docs = _person_docs(conn) if k == "people" else _work_docs(conn)
        log(f"  {k}: {len(docs)} 条待编码")
        t0 = time.time()
        conn.execute("DELETE FROM vectors WHERE kind=?", (k,))
        done = 0
        for i in range(0, len(docs), batch):
            chunk = docs[i:i + batch]
            vecs = encode([d for _id, d in chunk])
            conn.executemany(
                "INSERT OR REPLACE INTO vectors(kind,ref_id,dim,vec) VALUES(?,?,?,?)",
                [(k, rid, int(v.shape[0]), to_blob(v)) for (rid, _t), v in zip(chunk, vecs)])
            done += len(chunk)
            if done % 640 == 0:
                conn.commit()
                log(f"    {done}/{len(docs)}  {time.time()-t0:.0f}s")
        conn.commit()
        stats[k] = {"docs": len(docs), "seconds": round(time.time() - t0, 1)}
        log(f"  {k} 完成: {stats[k]}")
    conn.close()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="people", choices=["people", "works", "all"])
    args = ap.parse_args()
    print(build(args.kind, log=lambda s: print(s, flush=True)))
