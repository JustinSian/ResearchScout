#!/usr/bin/env bash
echo "== per_page=200 的单次成本 =="
curl -s -D /tmp/h2.txt -o /tmp/w200.json -m 40 \
  "https://api.openalex.org/works?filter=type:article&per_page=200&sort=cited_by_count:desc&mailto=scholartrust@example.com"
grep -iE 'x-ratelimit-(cost-usd|remaining|credits-used)' /tmp/h2.txt | head -5
echo "  返回条数: $(.venv/bin/python -c "import json;print(len(json.load(open('/tmp/w200.json'))['results']))" 2>/dev/null || echo '?')"
echo "  文件大小: $(du -h /tmp/w200.json | cut -f1)"
echo
echo "== 单篇 work 的字段是否够建人（看第一个 result 的键） =="
/home/<user>/scholar-trust/.venv/bin/python - <<'PY'
import json
d = json.load(open('/tmp/w200.json'))
r = d['results'][0]
print("  work keys:", sorted(r.keys())[:18])
au = (r.get('authorships') or [{}])[0].get('author', {})
print("  author keys:", sorted(au.keys()))
print("  author 样例:", {k: au.get(k) for k in ('id','display_name','orcid')})
PY
