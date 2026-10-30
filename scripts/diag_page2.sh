#!/usr/bin/env bash
echo "== select + page 翻页（无 cursor） =="
B='https://api.openalex.org/works?filter=type:article,has_abstract:true&sort=cited_by_count:desc&per_page=200&mailto=scholartrust@example.com&select=id,doi,display_name,publication_year,cited_by_count,type,primary_location,authorships,abstract_inverted_index,concepts'
for suffix in 'page=1' 'page=2' 'page=5' 'page=20'; do
  code=$(curl -s -o /tmp/p2.json -m 60 -w '%{http_code}' "$B&$suffix")
  n=$(.venv/bin/python -c "import json;d=json.load(open('/tmp/p2.json'));print(len(d.get('results') or []), (d.get('results') or [{}])[0].get('display_name','')[:40])" 2>/dev/null || echo '?')
  echo "  [$code] $suffix -> $n"
done
