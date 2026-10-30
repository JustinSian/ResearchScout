#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python

echo "== 重判（分位数阈值） =="
"$PY" -m app.rejudge 2>&1 | tail -3
echo
echo "== 重判后的分布 =="
"$PY" - <<'PY'
from app.db import connect
c = connect()
print("  状态分布:")
for r in c.execute("SELECT status, COUNT(*) n FROM people GROUP BY status ORDER BY n DESC"):
    print(f"    {r['status']:<10} {r['n']:>6}")
print("  关键切片:")
for label, sql in [
    ("published & works>=2", "SELECT COUNT(*) FROM people WHERE status='published' AND works_count>=2"),
    ("published & works>=5", "SELECT COUNT(*) FROM people WHERE status='published' AND works_count>=5"),
]:
    print(f"    {label:<24}", c.execute(sql).fetchone()[0])
PY
echo
echo "== 重启 API 并验证 min_works 生效 =="
bash scripts/start_api.sh >/dev/null 2>&1
sleep 2
curl -s -m 10 "http://127.0.0.1:8020/people?q=machine%20learning&limit=5" \
  | "$PY" -c "import json,sys;d=json.load(sys.stdin);print('  count',d['count']);[print('   ',i['name'],'| works',i['works_count'],'| cites',i['cited_by_count'],'| conf',i['confidence']) for i in d['items']]"
echo "== 名人仍应命中 =="
for q in "Yoshua Bengio" "Geoffrey Hinton" "Yann LeCun"; do
  curl -s -m 10 --get --data-urlencode "q=$q" "http://127.0.0.1:8020/people?limit=2" \
    | "$PY" -c "import json,sys;d=json.load(sys.stdin);print('  $q ->',[ (i['name'],i['works_count']) for i in d['items']])"
done
