"""证据强度分级 + 引擎降级明示。"""
from __future__ import annotations

import pytest

from app import agent_llm


def _ev(n):
    return [{"tool": "find_expert", "count": n, "items": [{"pid": i} for i in range(n)]}]


def test_no_evidence():
    assert agent_llm._evidence_strength([], True) == "none"
    assert agent_llm._evidence_strength([{"items": []}], False) == "none"
    assert agent_llm._evidence_strength(_ev(2), True) == "none"   # 拒答优先


def test_one_or_two_items_medium():
    assert agent_llm._evidence_strength(_ev(1), False) == "medium"
    assert agent_llm._evidence_strength(_ev(2), False) == "medium"


def test_three_or_more_high():
    assert agent_llm._evidence_strength(_ev(3), False) == "high"
    assert agent_llm._evidence_strength(_ev(5), False) == "high"


def test_degraded_reason_when_llm_disabled(monkeypatch):
    import app.api as api
    from app import agent_llm as al, qa_cache

    monkeypatch.setattr(qa_cache, "get", lambda q, uid="": None)
    monkeypatch.setattr(al, "enabled", lambda: False)
    monkeypatch.setattr(api, "supervise",
                        lambda q, sid="", uid="": {"ok": True, "answer": "规则答案",
                                                   "citations": [], "refused": False})
    r = api._timed_supervise("找专家", "s1", "u1")
    assert r["engine"] == "classic"
    assert r["degraded"] == "LLM 未配置"
