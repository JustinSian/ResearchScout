#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python
echo "== /graph/122 (Bengio 的 1 跳合作网) =="
curl -s -m 15 "http://127.0.0.1:8020/graph/122?depth=1" \
  | "$PY" -c "
import json,sys
d=json.load(sys.stdin)
print('  nodes',len(d['nodes']),'edges',len(d['edges']))
for n in d['nodes'][:6]: print('   ', n.get('name'), '|', n.get('last_inst'), '| depth', n.get('depth'))"
echo
echo "== 库内分布（单篇作者占比 / 多篇作者） =="
"$PY" - <<'PY'
from app.db import connect
c = connect()
for label, sql in [
    ("作品数 >= 2 的人", "SELECT COUNT(*) FROM people WHERE works_count>=2"),
    ("作品数 >= 5 的人", "SELECT COUNT(*) FROM people WHERE works_count>=5"),
    ("作品数 = 1 的人", "SELECT COUNT(*) FROM people WHERE works_count=1"),
    ("被引 >= 1000 的人", "SELECT COUNT(*) FROM people WHERE cited_by_count>=1000"),
    ("published 且作品>=2", "SELECT COUNT(*) FROM people WHERE status='published' AND works_count>=2"),
]:
    print(f"  {label:<18}", c.execute(sql).fetchone()[0])
PY
