#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"

echo "== 清空重采（works-first，40 页 = 8000 篇） =="
rm -f data/scholar.db data/scholar.db-wal data/scholar.db-shm
"$PY" -m app.ingest_works 2>&1 | tail -12

echo
echo "== 统计与门槛分布 =="
"$PY" - <<'PY'
from app.db import connect, stats
c = connect()
print(stats(c))
print("\n人物置信度分布:")
for r in c.execute("""SELECT verdict, COUNT(*) n, ROUND(AVG(score),3) s
                      FROM candidates GROUP BY verdict ORDER BY n DESC"""):
    print(f"  {r['verdict']:<7} {r['n']:>6} 条  平均 {r['s']}")
print("\n样本（高分/低分）:")
for r in c.execute("""SELECT name, works_count, cited_by_count, confidence, status
                      FROM people ORDER BY confidence DESC LIMIT 3"""):
    print(f"  ✓ {r['name'][:34]:<34} works={r['works_count']:<3} cites={r['cited_by_count']:<6} {r['confidence']}")
for r in c.execute("""SELECT name, works_count, cited_by_count, confidence, status
                      FROM people ORDER BY confidence ASC LIMIT 5"""):
    print(f"  ✗ {r['name'][:34]:<34} works={r['works_count']:<3} cites={r['cited_by_count']:<6} {r['confidence']}")
PY
