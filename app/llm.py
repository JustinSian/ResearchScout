"""极简 DeepSeek 客户端：只做一件事——把 prompt 换成 JSON。

为什么不用官方 SDK：
- 只要一个 `/chat/completions`，用 httpx 就够，少一个依赖；
- 需要把 token 用量和耗时一起返回（成本分析要用），SDK 反而绕。

配置（全部走环境变量，也支持项目根目录的 .env）：
  DEEPSEEK_API_KEY    必填；没有就 `configured()` 返回 False，调用方退回规则路径
  DEEPSEEK_BASE_URL   默认 https://api.deepseek.com
  DEEPSEEK_MODEL      默认 deepseek-flash

**不要把 key 写进代码或提交**：.env 已在 .gitignore 里。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"

# 单价（美元 / 1M tokens）。DeepSeek 分峰谷，这里取偏贵的峰值价，宁可高估成本。
PRICE_INPUT = float(os.environ.get("DEEPSEEK_PRICE_INPUT", "0.30"))
PRICE_OUTPUT = float(os.environ.get("DEEPSEEK_PRICE_OUTPUT", "1.20"))

_ENV_LOADED = False


def load_env(path: str | Path | None = None) -> None:
    """加载 .env（KEY=VALUE，# 注释）。不覆盖已存在的环境变量。"""
    global _ENV_LOADED
    p = Path(path or os.environ.get("ST_ENV_FILE") or
             Path(__file__).resolve().parents[1] / ".env")
    if not p.exists():
        _ENV_LOADED = True
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
    _ENV_LOADED = True


def _ensure_env() -> None:
    if not _ENV_LOADED:
        load_env()


def api_key() -> str:
    _ensure_env()
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def model() -> str:
    _ensure_env()
    return (os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()


def base_url() -> str:
    _ensure_env()
    return (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE).strip().rstrip("/")


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return round((prompt_tokens or 0) / 1e6 * PRICE_INPUT
                 + (completion_tokens or 0) / 1e6 * PRICE_OUTPUT, 8)


def chat_json(system: str, user: str, *, timeout: float = 40.0,
              retries: int = 2, temperature: float = 0.0) -> dict:
    """调用 DeepSeek，要求返回 JSON 对象。

    成功：{"ok": True, "data": {...}, "usage": {...}, "cost_usd": .., "ms": ..}
    失败：{"ok": False, "error": "...", "ms": ..}（调用方负责退回规则路径）
    """
    if not configured():
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {
        "model": model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "max_tokens": 800,
    }
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    last_err = ""
    t0 = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{base_url()}/chat/completions",
                                json=payload, headers=headers)
            if r.status_code in (401, 403):
                return {"ok": False, "error": f"鉴权失败 {r.status_code}",
                        "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {},
                        "fatal": True}
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            usage = body.get("usage") or {}
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            try:
                data = json.loads(_strip_fence(text))
            except Exception as exc:
                return {"ok": False, "error": f"返回不是合法 JSON: {exc}",
                        "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "usage": usage, "raw": text[:300]}
            return {"ok": True, "data": data,
                    "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                    "cost_usd": cost_usd(pt, ct),
                    "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0 * (attempt + 1))
    return {"ok": False, "error": last_err or "未知错误",
            "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {}}


def _strip_fence(text: str) -> str:
    """模型偶尔裹 ```json ... ```，剥掉再解析。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def chat_with_tools(messages: list[dict], tools: list[dict], *, timeout: float = 60.0,
                    retries: int = 1, temperature: float = 0.0) -> dict:
    """带工具的一轮对话（function calling）。

    返回：{"ok":True,"message":{...},"usage":{...},"cost_usd":..,"ms":..}
    message 里可能有 tool_calls，交由调用方执行后把结果塞回 messages。
    """
    if not configured():
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {"model": model(), "messages": messages, "temperature": temperature,
               "tools": tools, "tool_choice": "auto", "max_tokens": 1200}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    last_err = ""
    t0 = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(f"{base_url()}/chat/completions",
                                json=payload, headers=headers)
            if r.status_code in (401, 403):
                return {"ok": False, "error": f"鉴权失败 {r.status_code}", "fatal": True,
                        "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {}}
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            msg = (body.get("choices") or [{}])[0].get("message") or {}
            usage = body.get("usage") or {}
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            return {"ok": True, "message": msg,
                    "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                    "cost_usd": cost_usd(pt, ct),
                    "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0 * (attempt + 1))
    return {"ok": False, "error": last_err or "未知错误",
            "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {}}


def chat_text(messages: list[dict], *, timeout: float = 60.0) -> dict:
    """不带工具的一轮对话：用于"强制收尾作答"。"""
    if not configured():
        return {"ok": False, "error": "DEEPSEEK_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {"model": model(), "messages": messages, "temperature": 0.0,
               "max_tokens": 1000}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{base_url()}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()
        msg = (body.get("choices") or [{}])[0].get("message") or {}
        usage = body.get("usage") or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        return {"ok": True, "text": (msg.get("content") or "").strip(),
                "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                "cost_usd": cost_usd(pt, ct),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {}}
