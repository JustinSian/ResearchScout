import json

from app.db import connect
from app.search import search_people


def main() -> None:
    gold = json.load(open("eval/gold.json", encoding="utf-8"))
    conn = connect()
    for tier in ("topic", "institution"):
        g = next(x for x in gold if x["tier"] == tier)
        print(f"== {tier}: {g['query']}  期望 {len(g['expected'])} 个 ==")
        for pid in g["expected"]:
            r = conn.execute("SELECT name,last_inst,works_count,cited_by_count,topics "
                             "FROM people WHERE pid=?", (pid,)).fetchone()
            print(f"   期望 pid={pid} {r['name'][:26]:<26} inst={str(r['last_inst'])[:26]:<26} "
                  f"cites={r['cited_by_count']} topics={json.loads(r['topics'] or '[]')[:2]}")
        hits = search_people(g["query"], limit=10)
        print(f"   实际 top10: {[h[0] for h in hits]}")
        print(f"   命中: {sorted(set(h[0] for h in hits) & set(g['expected']))}")


if __name__ == "__main__":
    main()
