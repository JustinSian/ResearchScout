"""重建 FTS 索引，改用 porter 词干化。

原因：FTS5 默认 unicode61 不做词干。
  单词命中 graph=169 / neural=246 / networks=17，但三者 AND 起来是 0——
  因为 OpenAlex 的主题名是 "Graph Neural Network"（单数）。
对英文语料，porter 词干化能显著提升召回；代价是少数专有名词可能被过度截断。
"""
from __future__ import annotations

from app.db import connect


def main() -> None:
    conn = connect()
    for t in ("people_fts", "works_fts"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("""CREATE VIRTUAL TABLE people_fts USING fts5(
        name, topics, inst, tokenize='porter unicode61')""")
    conn.execute("""CREATE VIRTUAL TABLE works_fts USING fts5(
        title, abstract, tokenize='porter unicode61')""")

    import json
    for r in conn.execute("SELECT pid,name,topics,last_inst FROM people"):
        topics = " ".join(json.loads(r["topics"] or "[]"))
        conn.execute("INSERT INTO people_fts(rowid,name,topics,inst) VALUES(?,?,?,?)",
                     (r["pid"], r["name"], topics, r["last_inst"] or ""))
    for w in conn.execute("SELECT wid,title,abstract FROM works"):
        conn.execute("INSERT INTO works_fts(rowid,title,abstract) VALUES(?,?,?)",
                     (w["wid"], w["title"] or "", w["abstract"] or ""))
    conn.commit()
    print("people_fts:", conn.execute("SELECT COUNT(*) FROM people_fts").fetchone()[0])
    print("works_fts:", conn.execute("SELECT COUNT(*) FROM works_fts").fetchone()[0])
    for q in ('"network"', '"graph" AND "neural" AND "network"', '"dementia"'):
        n = conn.execute("SELECT COUNT(*) FROM people_fts WHERE people_fts MATCH ?",
                         (q,)).fetchone()[0]
        print(f"  {q:<36} -> {n}")
    conn.close()


if __name__ == "__main__":
    main()
