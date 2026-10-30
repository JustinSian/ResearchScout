import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.openalex.org"
UA = "ScholarTrust/0.1 (mailto:scholartrust@example.com)"
SELECT = ("id,doi,display_name,publication_year,cited_by_count,type,"
          "primary_location,authorships,abstract_inverted_index,concepts,"
          "referenced_works_count")

variants = {
    "A 原始（含 select+cursor）": {
        "filter": "type:article,has_abstract:true", "sort": "cited_by_count:desc",
        "per_page": 200, "cursor": "*", "select": SELECT,
        "mailto": "scholartrust@example.com"},
    "B 去掉 cursor": {
        "filter": "type:article,has_abstract:true", "sort": "cited_by_count:desc",
        "per_page": 200, "select": SELECT, "mailto": "scholartrust@example.com"},
    "C 去掉 select": {
        "filter": "type:article,has_abstract:true", "sort": "cited_by_count:desc",
        "per_page": 200, "cursor": "*", "mailto": "scholartrust@example.com"},
    "D per_page=50": {
        "filter": "type:article,has_abstract:true", "sort": "cited_by_count:desc",
        "per_page": 50, "cursor": "*", "select": SELECT,
        "mailto": "scholartrust@example.com"},
}

for label, params in variants.items():
    url = f"{BASE}/works?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read(200).decode("utf-8", "ignore")
            print(f"{label}: {resp.status} cost={resp.headers.get('x-ratelimit-cost-usd')} "
                  f"remaining={resp.headers.get('x-ratelimit-remaining')}")
            print(f"    body: {body[:120]}")
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", "ignore")
        print(f"{label}: HTTP {exc.code} remaining={exc.headers.get('x-ratelimit-remaining')}")
        print(f"    body: {body[:220]}")
