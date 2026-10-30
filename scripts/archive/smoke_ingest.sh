#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"

echo "== 冒烟：先采 40 人验证链路 =="
ST_AUTHORS=40 ST_WORKS=20 "$PY" -m app.ingest 2>&1 | tail -8
echo
echo "== 统计 =="
"$PY" - <<'PY'
from app.db import connect, stats
c = connect()
print(stats(c))
rows = c.execute("SELECT name,last_inst,status,confidence FROM people ORDER BY confidence DESC LIMIT 5").fetchall()
print("高分样本:")
for r in rows:
    print("  ", r["name"], "|", r["last_inst"], "|", r["status"], round(r["confidence"],2))
rows = c.execute("SELECT payload,confidence,reasons FROM candidates WHERE verdict IN ('review','reject') ORDER BY confidence LIMIT 5").fetchall()
print("拒收样本:")
for r in rows:
    print("  ", r["payload"][:70], round(r["confidence"],2), r["reasons"][:80])
PY
