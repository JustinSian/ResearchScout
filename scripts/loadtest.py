"""轻量压测：/search（检索）与 /chat（Agent，含 CE 打分）在不同并发下的 p50/p95。"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time

import httpx

BASE = "http://127.0.0.1:8020"
SEARCH_Q = "graph neural network"
CHAT_Q = "谁和 Yoshua Bengio 合作过"


async def one(c, path, params):
    t0 = time.perf_counter()
    r = await c.get(path, params=params, timeout=120)
    return r.status_code, (time.perf_counter() - t0) * 1000


async def run(concurrency: int, total: int, path: str, params: dict):
    async with httpx.AsyncClient(base_url=BASE,
                                 limits=httpx.Limits(max_connections=concurrency + 4)) as c:
        todo = list(range(total))

        async def worker():
            out = []
            while todo:
                todo.pop()
                out.append(await one(c, path, params))
            return out

        t0 = time.perf_counter()
        res = [r for chunk in await asyncio.gather(*[worker() for _ in range(concurrency)])
               for r in chunk]
        wall = time.perf_counter() - t0
    lat = sorted(ms for code, ms in res if code == 200)
    ok = len(lat)
    p = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))] if lat else 0
    print(f"  {path:<9} c={concurrency:<3} 请求 {total:<4} 成功 {ok}/{total}  "
          f"吞吐 {ok/wall:5.1f} rps  p50 {p(.5):6.0f}ms  p95 {p(.95):6.0f}ms  "
          f"p99 {p(.99):6.0f}ms")


async def main():
    for path, params in (("/search", {"q": SEARCH_Q, "limit": 10}),
                         ("/chat", {"q": CHAT_Q})):
        for c in (1, 4, 8, 16):
            await run(c, max(40, c * 20), path, params)


if __name__ == "__main__":
    asyncio.run(main())
