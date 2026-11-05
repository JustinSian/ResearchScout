"""问答缓存：只缓存成功且非拒答的答案；语料/记忆/TTL 任一变化即失效。"""
from __future__ import annotations

import time

import pytest

from app import memory, qa_cache


@pytest.fixture()
def fake_kv(monkeypatch):
    """用内存字典顶掉 kv 读写，避免测试污染 demo.db。"""
    store: dict = {}

    def _get(key):
        v = store.get(key)
        if not v:
            return None
        return dict(v)

    def _put(key, value):
        # 真实实现里 updated_at 由 kv 行给出，这里模拟一下
        store[key] = {**value, "updated_at": time.time()}

    monkeypatch.setattr(memory, "cache_get", _get)
    monkeypatch.setattr(memory, "cache_put", _put)
    return store


def test_norm_ignores_punctuation_and_space(fake_kv):
    assert qa_cache.key("找 专家") == qa_cache.key("找专家")
    assert qa_cache.key("找专家。") == qa_cache.key("找专家")


def test_roundtrip_sets_cached_and_free(fake_kv):
    payload = {"ok": True, "answer": "有答案", "citations": [], "cost_usd": 0.01}
    assert qa_cache.put("介绍一下钟南山", "u1", payload) is True
    got = qa_cache.get("介绍一下钟南山", "u1")
    assert got and got["answer"] == "有答案"
    assert got["cached"] is True and got["cost_usd"] == 0.0


def test_refused_not_cached(fake_kv):
    assert qa_cache.put("q", "u", {"ok": True, "refused": True, "answer": "没找到"}) is False
    assert qa_cache.get("q", "u") is None


def test_timeout_and_cancel_not_cached(fake_kv):
    assert qa_cache.put("q", "u", {"ok": True, "finish_reason": "timeout"}) is False
    assert qa_cache.put("q", "u", {"ok": True, "cancelled": True}) is False
    assert qa_cache.get("q", "u") is None


def test_failed_not_cached(fake_kv):
    assert qa_cache.put("q", "u", {"ok": False}) is False


def test_ttl_expiry(fake_kv):
    qa_cache.put("q", "u", {"ok": True, "answer": "x"})
    k = qa_cache.key("q", "u")
    fake_kv[k]["updated_at"] = time.time() - qa_cache.DEFAULT_TTL - 10
    assert qa_cache.get("q", "u") is None


def test_different_user_isolated(fake_kv):
    qa_cache.put("q", "alice", {"ok": True, "answer": "A"})
    assert qa_cache.get("q", "bob") is None
