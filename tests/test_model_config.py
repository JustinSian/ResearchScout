"""运行时模型配置的测试：校验、读写、脱敏、环境变量应用。"""
from __future__ import annotations

import json

import pytest

from app import model_config


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(model_config, "CONFIG_PATH", tmp_path / "model_config.json")
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_JSON_MODE",
              "LLM_PRICE_INPUT", "LLM_PRICE_OUTPUT"):
        monkeypatch.delenv(k, raising=False)
    return model_config


def test_validate_rejects_bad_input():
    assert model_config.validate({"base_url": "ftp://x"})[0] is False
    # 空字符串表示"清除该字段"，不应被当成非法值
    assert model_config.validate({"model": ""})[0] is True
    assert model_config.validate({"json_mode": "yaml"})[0] is False
    assert model_config.validate({"price_in": "-1"})[0] is False
    assert model_config.validate({"base_url": "https://api.openai.com/v1",
                                  "model": "gpt-4o-mini"})[0] is True


def test_save_load_roundtrip(cfg):
    cfg.save({"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
              "api_key": "sk-test-1234"})
    got = cfg.load()
    assert got["model"] == "gpt-4o-mini"
    assert json.loads(cfg.CONFIG_PATH.read_text(encoding="utf-8"))["api_key"] == "sk-test-1234"


def test_empty_value_clears_field(cfg):
    cfg.save({"model": "m1"})
    cfg.save({"model": ""})
    assert "model" not in cfg.load()


def test_status_never_exposes_plaintext_key(cfg, monkeypatch):
    cfg.save({"base_url": "https://x.test/v1", "model": "m", "api_key": "sk-secret-abcd"})
    st = cfg.status()
    blob = json.dumps(st, ensure_ascii=False)
    assert "sk-secret-abcd" not in blob
    assert st["has_key"] is True
    assert st["key_hint"].endswith("abcd")
    assert st["source"] == "file"


def test_apply_sets_env(cfg):
    cfg.save({"base_url": "https://x.test/v1", "model": "m", "json_mode": "text"})
    cfg.apply(cfg.load())
    import os
    assert os.environ["LLM_MODEL"] == "m"
    assert os.environ["LLM_BASE_URL"] == "https://x.test/v1"
    assert os.environ["LLM_JSON_MODE"] == "text"
