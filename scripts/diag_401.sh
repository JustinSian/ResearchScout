#!/usr/bin/env bash
echo "== 分别测两个 filter =="
for f in 'has_orcid:true,works_count:>20' 'works_count:>8' 'has_orcid:true'; do
  code=$(curl -s -o /tmp/oal.json -m 20 -w '%{http_code}' \
    "https://api.openalex.org/authors?filter=${f}&per_page=1&mailto=scholartrust@example.com")
  echo "  [$code] filter=$f"
  [ "$code" != "200" ] && head -c 200 /tmp/oal.json && echo
done
echo
echo "== 带上 select 再试 =="
code=$(curl -s -o /tmp/oal2.json -m 20 -w '%{http_code}' \
  "https://api.openalex.org/works?filter=author.id:A5108093963&per_page=1&select=id,doi,title,publication_year,cited_by_count,type,primary_location,authorships,abstract_inverted_index&mailto=scholartrust@example.com")
echo "  [$code] works+select"
head -c 200 /tmp/oal2.json; echo
