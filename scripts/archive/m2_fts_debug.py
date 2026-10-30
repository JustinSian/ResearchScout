from app.db import connect
from app.search import _fts_query, search_people


def main() -> None:
    conn = connect()
    print("FTS 行数:", conn.execute("SELECT COUNT(*) FROM people_fts").fetchone()[0])
    print("published 人数:", conn.execute("SELECT COUNT(*) FROM people WHERE status='published'").fetchone()[0])
    q = "graph neural networks"
    print("查询式:", _fts_query(q))
    try:
        n = conn.execute("SELECT COUNT(*) FROM people_fts WHERE people_fts MATCH ?",
                         (_fts_query(q),)).fetchone()[0]
        print("FTS 直接匹配行数:", n)
    except Exception as exc:
        print("FTS 报错:", exc)
    # 看一个主题里确实含 Graph Neural Networks 的人
    rows = conn.execute(
        """SELECT pid,name,topics FROM people WHERE topics LIKE '%"Graph Neural Networks"%'
           AND status='published' LIMIT 3""").fetchall()
    for r in rows:
        print("  样例:", r["pid"], r["name"], r["topics"][:80])
    # 单独词测试
    for w in ("graph", "neural", "networks", "Dementia", "machine"):
        n = conn.execute("SELECT COUNT(*) FROM people_fts WHERE people_fts MATCH ?",
                         (f'"{w}"',)).fetchone()[0]
        print(f"  单词 {w:<10} 命中 {n}")
    print("search_people:", len(search_people(q, limit=5)))
    conn.close()


if __name__ == "__main__":
    main()
