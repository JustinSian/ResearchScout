"""极简 OpenAI 兼容客户端：只做一件事——把 prompt 换成 JSON / 跑工具调用。

为什么不用官方 SDK：
- 只要一个 `/chat/completions`，用 httpx 就够，少一个依赖；
- 需要把 token 用量和耗时一起返回（成本分析要用），SDK 反而绕。

配置（全部走环境变量，也支持项目根目录的 .env）。
**支持任何 OpenAI 兼容端点**（OpenAI / DeepSeek / Moonshot / 智谱 GLM / 通义 Qwen /
OpenRouter / vLLM / LM Studio / Ollama 等），用中性别名即可：

  LLM_API_KEY       必填；没有就 configured() 返回 False，调用方退回规则路径
  LLM_BASE_URL      默认 https://api.deepseek.com
  LLM_MODEL         默认 deepseek-flash
  LLM_PRICE_INPUT   美元/1M input tokens（默认 DeepSeek 峰值价 0.30）
  LLM_PRICE_OUTPUT  美元/1M output tokens（默认 1.20）
  LLM_JSON_MODE     json_object（默认）| text（供不支持 response_format 的提供商）

兼容旧名：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL /
          DEEPSEEK_PRICE_INPUT / DEEPSEEK_PRICE_OUTPUT 仍然有效，作为回退。

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


def _pick(name: str, legacy: str | None, default: str) -> str:
    _ensure_env()
    return (os.environ.get(name) or (os.environ.get(legacy) if legacy else "") or default).strip()


def api_key() -> str:
    return _pick("LLM_API_KEY", "DEEPSEEK_API_KEY", "")


def configured() -> bool:
    return bool(api_key())


def model() -> str:
    return _pick("LLM_MODEL", "DEEPSEEK_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    return _pick("LLM_BASE_URL", "DEEPSEEK_BASE_URL", DEFAULT_BASE).rstrip("/")


def json_mode() -> str:
    _ensure_env()
    return (os.environ.get("LLM_JSON_MODE") or "json_object").strip()


def price_input() -> float:
    _ensure_env()
    return float(os.environ.get("LLM_PRICE_INPUT") or os.environ.get("DEEPSEEK_PRICE_INPUT") or "0.30")


def price_output() -> float:
    _ensure_env()
    return float(os.environ.get("LLM_PRICE_OUTPUT") or os.environ.get("DEEPSEEK_PRICE_OUTPUT") or "1.20")


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return round((prompt_tokens or 0) / 1e6 * price_input()
                 + (completion_tokens or 0) / 1e6 * price_output(), 8)


def chat_json(system: str, user: str, *, timeout: float = 40.0,
              retries: int = 2, temperature: float = 0.0) -> dict:
    """调用模型，要求返回 JSON 对象。

    成功：{"ok": True, "data": {...}, "usage": {...}, "cost_usd": .., "ms": ..}
    失败：{"ok": False, "error": "...", "ms": ..}（调用方负责退回规则路径）
    """
    if not configured():
        return {"ok": False, "error": "LLM_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {
        "model": model(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": 800,
    }
    if json_mode() == "json_object":
        payload["response_format"] = {"type": "json_object"}
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
        return {"ok": False, "error": "LLM_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {"model": model(), "messages": messages, "temperature": temperature,
               "tools": tools, "tool_choice": "auto", "max_tokens": 1200}
    # 允许模型一轮返回多个工具调用（profile+research_area+works 同时发），
    # 把 3 次串行 LLM 往返压成 1 次。个别提供商不认这个参数，400 时自动回退。
    if os.environ.get("ST_PARALLEL_TOOLS", "1") != "0":
        payload["parallel_tool_calls"] = True  # 允许模型在一次响应里提出多个工具调用
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
            if r.status_code == 400 and "parallel_tool_calls" in payload:
                # 提供商不认这个参数 → 去掉重试（不是错误，只是少了并行优化）
                payload.pop("parallel_tool_calls", None)
                last_err = "parallel_tool_calls 不被支持，已回退"
                continue
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
        return {"ok": False, "error": "LLM_API_KEY 未配置", "ms": 0, "usage": {}}
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


def chat_stream(messages: list[dict], *, timeout: float = 120.0,
                on_delta=None, max_tokens: int = 1200) -> dict:
    """流式的一轮对话：逐 token 回调 on_delta，返回结构与 chat_text 一致。

    为什么需要：非流式时用户要等整段算完（p50 7–8 秒）才看到第一个字；
    流式后首 token 通常 1 秒内到达，感知延迟大幅下降。

    - on_delta(piece) 抛异常不影响主流程（前端断开时不该拖垮后端）；
    - 任何异常都退回 chat_text，保证"至少能出答案"。
    """
    if not configured():
        return {"ok": False, "error": "LLM_API_KEY 未配置", "ms": 0, "usage": {}}
    payload = {"model": model(), "messages": messages, "temperature": 0.0,
               "max_tokens": max_tokens, "stream": True}
    # 有的提供商不支持 stream_options，失败会自动退回非流式
    payload["stream_options"] = {"include_usage": True}
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    parts: list[str] = []
    pt = ct = 0
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{base_url()}/chat/completions",
                               json=payload, headers=headers) as r:
                if r.status_code >= 400:
                    r.read()
                    raise RuntimeError(f"HTTP {r.status_code}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    elif line.startswith("{"):
                        data = line.strip()
                    else:
                        continue
                    if data == "[DONE]":
                        break
                    try:
                        ev = json.loads(data)
                    except Exception:
                        continue
                    u = ev.get("usage") or {}
                    if u:
                        pt = int(u.get("prompt_tokens") or 0) or pt
                        ct = int(u.get("completion_tokens") or 0) or ct
                    choice = (ev.get("choices") or [{}])[0] or {}
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        parts.append(piece)
                        if on_delta:
                            try:
                                on_delta(piece)
                            except Exception:
                                pass
        text = "".join(parts).strip()
        return {"ok": True, "text": text,
                "usage": {"prompt_tokens": pt, "completion_tokens": ct},
                "cost_usd": cost_usd(pt, ct),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:
        fb = chat_text(messages, timeout=timeout)
        if fb.get("ok"):
            fb["stream_fallback"] = True
            fb["stream_error"] = f"{type(exc).__name__}: {exc}"
            return fb
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "ms": round((time.perf_counter() - t0) * 1000, 1), "usage": {}}
