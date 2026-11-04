"""运行时模型配置：让用户不重启、不改 .env 就能换模型。

存放位置：`data/model_config.json`（在 `data/*` 的 gitignore 规则内，不会进仓库）。
优先级：**配置文件 > 环境变量 > 内置默认**。
安全：`status()` 永远不返回明文 key，只返回是否已配置 + 末四位。

⚠️ 单机部署说明：key 以明文存在本机 JSON 里（与 .env 同等信任级别）。
   多人/公网部署请改用密钥管理，或只允许通过环境变量注入。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "model_config.json"

FIELDS = ("base_url", "model", "api_key", "json_mode", "price_in", "price_out")
ENV_MAP = {
    "api_key": "LLM_API_KEY",
    "base_url": "LLM_BASE_URL",
    "model": "LLM_MODEL",
    "json_mode": "LLM_JSON_MODE",
    "price_in": "LLM_PRICE_INPUT",
    "price_out": "LLM_PRICE_OUTPUT",
}


def load() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: data[k] for k in FIELDS if data.get(k) not in (None, "")}


def validate(patch: dict) -> tuple[bool, str]:
    """校验非空字段；空字符串表示"清除该字段"，不参与校验。"""
    base = str(patch.get("base_url") or "").strip()
    if base and not base.startswith(("http://", "https://")):
        return False, "base_url 必须以 http:// 或 https:// 开头"
    mode = str(patch.get("json_mode") or "").strip()
    if mode and mode not in ("json_object", "text"):
        return False, "json_mode 只能是 json_object 或 text"
    for k in ("price_in", "price_out"):
        if patch.get(k) not in (None, ""):
            try:
                if float(patch[k]) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return False, f"{k} 必须是非负数字"
    return True, ""


def save(patch: dict) -> dict:
    """合并写入配置文件，返回新配置（不含明文 key 的对外视图请用 status()）。"""
    ok, why = validate(patch)
    if not ok:
        raise ValueError(why)
    cur = load()
    for k in FIELDS:
        if k not in patch:
            continue
        v = patch[k]
        if v in (None, ""):
            cur.pop(k, None)
        else:
            cur[k] = str(v).strip() if k != "api_key" else str(v).strip()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
    return cur


def apply(cfg: dict | None = None) -> None:
    """把配置写进当前进程的环境变量（llm 每次调用都重新读 os.environ）。"""
    cfg = load() if cfg is None else cfg
    for k, env in ENV_MAP.items():
        if cfg.get(k) not in (None, ""):
            os.environ[env] = str(cfg[k])


def _mask(key: str) -> str:
    if not key:
        return ""
    return "****" + key[-4:] if len(key) > 4 else "****"


def status() -> dict:
    """对外状态：不含明文 key。"""
    from app import llm

    file_cfg = load()
    # 先应用，保证 status 与真实调用一致
    try:
        apply(file_cfg)
    except Exception:
        pass
    key = llm.api_key()
    return {
        "model": llm.model(),
        "base_url": llm.base_url(),
        "has_key": bool(key),
        "key_hint": _mask(key),
        "json_mode": llm.json_mode(),
        "price_in": llm.price_input(),
        "price_out": llm.price_output(),
        "source": "file" if file_cfg else "env",
        "configured": llm.configured(),
    }


def test_connection(patch: dict | None = None) -> dict:
    """用给定配置（或当前配置）做一次最小真实调用，并探测工具调用能力。

    不落盘：只临时改环境变量，测完恢复。
    """
    from app import llm

    saved = {k: os.environ.get(k) for k in ENV_MAP.values()}
    cfg = dict(load())
    if patch:
        for k in FIELDS:
            if k in patch and patch[k] not in (None, ""):
                cfg[k] = patch[k]
    try:
        apply(cfg)
        probe = llm.chat_text([{"role": "user", "content": "请只回复：pong"}], timeout=25.0)
        if not probe.get("ok"):
            return {"ok": False, "error": probe.get("error") or "调用失败",
                    "base_url": llm.base_url(), "model": llm.model()}
        tool = {"type": "function", "function": {
            "name": "ping", "description": "健康检查", "parameters": {
                "type": "object", "properties": {"nonce": {"type": "string"}}}}}
        probe2 = llm.chat_with_tools(
            [{"role": "user", "content": "调用 ping 工具，nonce 传 ok"}], [tool], timeout=25.0)
        if probe2.get("ok"):
            called = bool((probe2.get("message") or {}).get("tool_calls"))
            support = "callable" if called else "unknown"
        else:
            err = str(probe2.get("error") or "")
            support = "unsupported" if any(w in err.lower() for w in
                                           ("tool", "function", "unsupported", "not support")) else "unknown"
        return {"ok": True, "reply": (probe.get("text") or "")[:120],
                "tool_calling": support, "base_url": llm.base_url(), "model": llm.model(),
                "ms": probe.get("ms")}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
