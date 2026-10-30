#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
Q="https://api.openalex.org/works?filter=type:article,has_abstract:true&sort=cited_by_count:desc&per_page=200&cursor=*&select=id,doi,display_name,publication_year,cited_by_count,type,primary_location,authorships,abstract_inverted_index,concepts,referenced_works_count&mailto=scholartrust@example.com"
curl -s -D /tmp/h3.txt -o /tmp/w3.json -m 60 "$Q"
echo "== 响应头 =="
grep -iE '^HTTP|x-ratelimit-(remaining|remaining-usd|cost-usd|reset)' /tmp/h3.txt | head -8
echo "== 响应体前 300 字 =="
head -c 300 /tmp/w3.json; echo
echo
echo "== 不带 select 再试一次 =="
curl -s -D /tmp/h4.txt -o /dev/null -m 60 \
  "https://api.openalex.org/works?filter=type:article&per_page=1&mailto=scholartrust@example.com"
grep -iE '^HTTP|x-ratelimit-(remaining|remaining-usd|reset)' /tmp/h4.txt | head -6
