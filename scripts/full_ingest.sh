#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"

echo "===== 第一遍：高置信（有 ORCID，works>20）400 人 ====="
ST_AUTHORS=400 ST_WORKS=40 ST_FILTER='has_orcid:true,works_count:>20' \
  "$PY" -m app.ingest 2>&1 | tail -6

echo
echo "===== 第二遍：宽松口径（无 ORCID 要求）150 人，喂可信门槛 ====="
ST_AUTHORS=150 ST_WORKS=20 ST_FILTER='works_count:>8' \
  "$PY" -m app.ingest 2>&1 | tail -6

echo
echo "===== 最终统计 ====="
"$PY" - <<'PY'
from app.db import connect, stats
c = connect()
print(stats(c))
print("\n置信度分布:")
for r in c.execute("""SELECT verdict, COUNT(*) n, ROUND(AVG(score),3) avg_score
                      FROM candidates GROUP BY verdict ORDER BY n DESC"""):
    print(f"  {r['verdict']:<7} {r['n']:>5} 条  平均分 {r['avg_score']}")
print("\n被拒/待审样本（前 6）:")
for r in c.execute("""SELECT payload, score, verdict, reasons FROM candidates
                      WHERE verdict != 'accept' ORDER BY score LIMIT 6"""):
    print(f"  [{r['verdict']}] {r['payload'][:60]} {r['score']:.2f}")
PY
