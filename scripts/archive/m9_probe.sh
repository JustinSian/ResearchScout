#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8

echo "== 1. 本地库里有没有钟南山 =="
.venv/bin/python - <<'PY'
from app.db import connect
c = connect()
for pat in ("%Nanshan%", "%Zhong%", "%钟南山%"):
    rows = c.execute("""SELECT pid,name,last_inst,works_count,cited_by_count,status
                        FROM people WHERE name LIKE ? LIMIT 5""", (pat,)).fetchall()
    print(f"  {pat}: {len(rows)} 条")
    for r in rows:
        print(f"     pid={r['pid']} {r['name']} | {r['last_inst']} | works {r['works_count']} "
              f"| cites {r['cited_by_count']} | {r['status']}")
PY

echo
echo "== 2. 某学术图谱服务可达性（10.x.x.x） =="
for u in "http://10.x.x.x:<port>/" "http://10.x.x.x:<port>/api/experts?page=1&page_size=3" \
         "http://10.x.x.x:<port>/api/v1/experts?page=1&page_size=3" \
         "http://10.x.x.x:<port>/docs" "http://10.x.x.x/"; do
  code=$(curl -s -o /tmp/hf.out -m 12 -w '%{http_code}' "$u")
  echo "  [$code] $u"
  [ "$code" = "200" ] && head -c 240 /tmp/hf.out && echo
done
