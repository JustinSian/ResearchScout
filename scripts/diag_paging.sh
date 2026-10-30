#!/usr/bin/env bash
echo "== cursor vs page 翻页 =="
B='https://api.openalex.org/works?filter=type:article,has_abstract:true&sort=cited_by_count:desc&per_page=200&mailto=scholartrust@example.com'
for suffix in 'cursor=*' 'page=1' 'page=2' 'page=3'; do
  code=$(curl -s -o /tmp/p.json -m 40 -w '%{http_code}' "$B&$suffix")
  n=$(.venv/bin/python -c "import json;d=json.load(open('/tmp/p.json'));print(len(d.get('results') or []), (d.get('meta') or {}).get('next_cursor'))" 2>/dev/null || echo '?')
  echo "  [$code] $suffix -> results/cursor: $n"
done
