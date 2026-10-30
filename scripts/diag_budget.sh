#!/usr/bin/env bash
echo "== 预算说明原文 =="
curl -s -m 20 "https://api.openalex.org/works?per_page=1&mailto=scholartrust@example.com" \
  | head -c 900
echo
echo
echo "== 剩余预算（若接口暴露） =="
curl -s -D /tmp/h.txt -o /dev/null -m 20 "https://api.openalex.org/works?per_page=1&mailto=scholartrust@example.com"
grep -iE 'ratelimit|x-budget|budget|remaining' /tmp/h.txt | head -10
echo
echo "== 当前库状态 =="
cd "$HOME/scholar-trust" && PYTHONPATH="$PWD" .venv/bin/python - <<'PY'
from app.db import connect, stats
c = connect()
print(stats(c))
PY
