"""LLM 配置解析的测试：中性 env 优先、旧名回退、定价、json 模式。"""
from __future__ import annotations

import app.llm as L


def _clear(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PRICE_INPUT",
              "LLM_PRICE_OUTPUT", "LLM_JSON_MODE", "DEEPSEEK_API_KEY",
              "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_PRICE_INPUT",
              "DEEPSEEK_PRICE_OUTPUT"):
        monkeypatch.delenv(k, raising=False)


def test_neutral_alias_preferred(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    assert L.model() == "gpt-4o-mini"


def test_legacy_fallback(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert L.base_url() == "https://api.deepseek.com"


def test_defaults_when_nothing_set(monkeypatch):
    _clear(monkeypatch)
    assert L.model() == "deepseek-flash"
    assert L.base_url() == "https://api.deepseek.com"
    assert L.api_key() == ""
    assert L.configured() is False


def test_custom_pricing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PRICE_INPUT", "2.5")
    monkeypatch.setenv("LLM_PRICE_OUTPUT", "10.0")
    assert L.cost_usd(1_000_000, 1_000_000) == 12.5


def test_json_mode_default_and_text(monkeypatch):
    _clear(monkeypatch)
    assert L.json_mode() == "json_object"
    monkeypatch.setenv("LLM_JSON_MODE", "text")
    assert L.json_mode() == "text"
