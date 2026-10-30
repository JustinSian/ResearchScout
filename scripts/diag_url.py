"""为什么 ingest 的第一个请求 401，而同样的参数在 diag 里 200？打印完整 URL 与错误体。"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from app.ingest_works import SELECT
from app.openalex import BASE, MAILTO, UA

params = {
    "filter": "type:article,has_abstract:true",
    "sort": "cited_by_count:desc",
    "per_page": 200,
    "cursor": "*",
    "select": SELECT,
    "mailto": MAILTO,
}
url = f"{BASE}/works?{urllib.parse.urlencode(params)}"
print("URL 长度:", len(url))
print("URL:", url[:220], "…")

for i in range(2):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  尝试{i+1}: 200，{len(data.get('results') or [])} 篇，"
                  f"remaining={resp.headers.get('x-ratelimit-remaining')}")
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode("utf-8", "ignore")
        print(f"  尝试{i+1}: HTTP {exc.code}")
        print("    headers:", {k: v for k, v in exc.headers.items()
                               if k.lower().startswith("x-ratelimit")})
        print("    body:", body[:300])
