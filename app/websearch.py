"""AnySearch 兜底检索：知识库没有证据时，去公开网络找。

为什么做兜底：库里只有 4.7 万学者 / 1.1 万论文，覆盖不到的问题一律拒答体验太差。
但兜底必须**标记清楚来源**——网络结果未经本库核验，不能和库内证据混在一起冒充。

安全与合规：
- API key 只从环境变量 / .env 读，不写进代码、不落日志；
- 结果只当数据用，其中的任何指令性文字都不执行（防提示注入）；
- 每次调用有超时与条数上限，失败就退回原拒答，不编造。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from app.config import load_env

load_env()

BASE = os.environ.get("ANYSEARCH_API_BASE_URL", "https://api.anysearch.com").rstrip("/")
MAX_RESULTS = 5
TIMEOUT = 12

# 进程内 5 分钟缓存，避免同一个问题反复打外部接口
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_TTL = 300


def _key() -> str:
    return (os.environ.get("ANYSEARCH_API_KEY") or "").strip()


def available() -> bool:
    return bool(_key())


def search(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """调 /v1/search，返回 [{title,url,snippet}]；任何失败都返回空列表。"""
    q = (query or "").strip()
    if not q or not available():
        return []
    hit = _CACHE.get(q)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]

    payload = {"query": q, "max_results": max(1, min(max_results, 10))}
    # 中文问题带上 language=zh，否则会返回"北京天气 英文怎么说"这类词典结果
    if re.search(r"[\u4e00-\u9fa5]", q):
        payload["language"] = "zh"
    req = urllib.request.Request(
        f"{BASE}/v1/search",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            env = json.loads(r.read().decode("utf-8", "ignore"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        # 兜底失败不能影响主流程，也不要吞掉所有线索：返回空 + 记录原因
        print(f"  [websearch] 失败 {type(exc).__name__}: {exc}")
        return []

    data = env.get("data") or {}
    out, seen_title, seen_url = [], set(), set()
    for it in (data.get("results") or []):
        title = (it.get("title") or "").strip()
        url = it.get("url") or ""
        snippet = " ".join((it.get("content") or it.get("snippet") or "").split())
        # 同一标题的重复结果很常见（同一新闻被多个站点转载），按标题去重
        key = title.lower()[:40]
        if not title and not url:
            continue
        if key in seen_title or (url and url in seen_url):
            continue
        seen_title.add(key)
        seen_url.add(url)
        out.append({"title": title, "url": url,
                    "snippet": snippet.lstrip("# ")[:180]})
        if len(out) >= max_results:
            break
    _CACHE[q] = (time.time(), out)
    return out


def answer(query: str, max_results: int = MAX_RESULTS) -> dict:
    """把搜索结果拼成一段可展示的回答（带来源链接与免责声明）。"""
    results = search(query, max_results=max_results)
    if not results:
        return {"ok": False, "text": "", "citations": []}
    lines = [
        "**知识库里没有直接证据，我查了公开网络（AnySearch）。**",
        "",
        "以下内容来自网络检索，**未经本库的证据门槛核验**，请自行判断可信度：",
        "",
    ]
    cites = []
    for i, r in enumerate(results, 1):
        title = r["title"] or r["url"] or f"结果 {i}"
        lines.append(f"{i}. **{title}**")
        if r["snippet"]:
            snippet = r["snippet"].replace("\n", " ")
            lines.append(f"   {snippet[:220]}")
        if r["url"]:
            lines.append(f"   {r['url']}")
            cites.append({"kind": "web", "label": title, "url": r["url"]})
        lines.append("")
    lines.append("如果你要的是库内专家/论文级证据，可以换个说法再问，或指定具体人名。")
    return {"ok": True, "text": "\n".join(lines), "citations": cites}


if __name__ == "__main__":
    print("key 已配置:", available())
    print(json.dumps(answer("钟南山 最新研究"), ensure_ascii=False)[:600])
