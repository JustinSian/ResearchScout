cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
bash scripts/start_api.sh >/dev/null 2>&1; sleep 3
URL=$(cat logs/public_url.txt)
for u in "/venues?top=3" "/venue/1" "/institutions?top=3" "/institutions/duplicates?limit=3" "/people/527/affiliation"; do
  echo -n "  $u → "
  curl -s -o /dev/null -w "%{http_code}\n" -m 40 "$URL$u"
done
echo "== analyst 是否带上机构与期刊 =="
.venv/bin/python -c "
from app.subagents import analyst
r = analyst([527])
a = r['authors'][0]
print('  机构:', a.get('institution'))
print('  期刊:', [(j['name'][:28], j['n']) for j in a.get('journals',[])])"