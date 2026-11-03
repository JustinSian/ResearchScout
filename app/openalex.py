"""OpenAlex 客户端：礼貌池 + 限流 + 重试 + 游标分页。

为什么不用现成 SDK：只需要 4 个端点，自己写能把限流与重试策略写清楚，
也避免为一个演示引入大依赖。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

BASE = "https://api.openalex.org"
# OpenAlex 礼貌池：带 mailto 会走更宽松的配额
MAILTO = "scholartrust@example.com"
UA = f"ScholarTrust/0.1 (mailto:{MAILTO})"

# API key：免费申请（POST /api-keys），激活后额度 $1/天（1 万次）而不是 $0.1/天（1 千次）。
# 未激活时带 key 会返回 403，所以这里做"失败即降级"：一旦 403 就永久关掉 key。
# 必须先加载 .env：否则 ST_OPENALEX_KEY 读不到（曾因此以为"没配置 key"）。
from app.config import load_env

load_env()
API_KEY = (os.environ.get("ST_OPENALEX_KEY") or "").strip()
_KEY_ACTIVE = bool(API_KEY)


def with_key(url: str) -> str:
    """给 OpenAlex URL 追加 api_key（未配置或已降级时原样返回）。"""
    if not _KEY_ACTIVE or not API_KEY:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}api_key={API_KEY}"


def disable_key(reason: str = "") -> None:
    """key 失效（未激活/被拒）时降级为无 key 模式，避免整条流水线挂掉。"""
    global _KEY_ACTIVE
    if _KEY_ACTIVE:
        _KEY_ACTIVE = False
        print(f"  [openalex] API key 停用（{reason or '403'}），改用无 key 模式")


@dataclass
class RateLimiter:
    min_interval: float = 0.12          # ~8 req/s，低于礼貌池上限
    _last: float = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


class OpenAlex:
    def __init__(self, limiter: RateLimiter | None = None):
        self.limiter = limiter or RateLimiter()

    def _get(self, path: str, params: dict[str, Any], retries: int = 4) -> dict:
        params = {**params, "mailto": MAILTO}
        base_url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        # 必须带 key：不带就走"全网络共享的 $0.10 免费额度"，很容易 401（实测踩过）
        url = with_key(base_url)
        last_err: Exception | None = None
        for attempt in range(retries):
            self.limiter.wait()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code in (401, 403):
                    # key 失效/被拒：降级为无 key 模式后重试（不能整条流水线挂掉）
                    disable_key(f"{exc.code}")
                    url = base_url
                    time.sleep(0.5)
                    continue
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                last_err = exc
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"OpenAlex 请求失败: {last_err}") from last_err

    # ---------------- 作者 ----------------
    def authors(
        self,
        *,
        per_page: int = 200,
        cursor: str = "*",
        filter_: str = "has_orcid:true,works_count:>20",
        sort: str = "cited_by_count:desc",
    ) -> dict:
        return self._get("/authors", {
            "filter": filter_, "sort": sort,
            "per_page": per_page, "cursor": cursor,
        })

    def iter_authors(self, *, limit: int, per_page: int = 200,
                     filter_: str = "has_orcid:true,works_count:>20",
                     sort: str = "cited_by_count:desc") -> Iterator[dict]:
        """按游标翻页，直到凑够 limit 个。"""
        cursor, got = "*", 0
        while got < limit:
            data = self.authors(per_page=per_page, cursor=cursor,
                                filter_=filter_, sort=sort)
            results = data.get("results") or []
            if not results:
                break
            for item in results:
                yield item
                got += 1
                if got >= limit:
                    return
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                return

    # ---------------- 论文 ----------------
    def works_of_author(self, author_id: str, per_page: int = 50) -> list[dict]:
        data = self._get("/works", {
            "filter": f"author.id:{author_id}",
            "per_page": per_page,
            "sort": "cited_by_count:desc",
            "select": ("id,doi,title,publication_year,cited_by_count,type,"
                       "primary_location,authorships,abstract_inverted_index"),
        })
        return data.get("results") or []


def abstract_from_inverted(inv: dict | None) -> str:
    """OpenAlex 只给倒排索引，这里还原成正文（用于全文检索）。"""
    if not inv:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in inv.items():
        for pos in positions:
            slots.append((int(pos), word))
    slots.sort()
    return " ".join(w for _, w in slots)
