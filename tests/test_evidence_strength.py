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


# ---- 来源必须参与判断：补库来的证据占多数时封顶 medium ----
def _ev_src(n_verified, n_auto):
    items = [{"pid": i} for i in range(n_verified)]
    items += [{"pid": 100 + i, "source": "auto_expand"} for i in range(n_auto)]
    return [{"tool": "find_expert", "count": len(items), "items": items}]


def test_auto_expand_majority_capped_at_medium():
    # 3 条全是补库来的 → 不能判 high
    assert agent_llm._evidence_strength(_ev_src(0, 3), False) == "medium"
    # 3 条里 2 条补库 → 封顶 medium
    assert agent_llm._evidence_strength(_ev_src(1, 2), False) == "medium"


def test_verified_majority_can_be_high():
    # 5 条里只有 1 条补库 → 仍可 high
    assert agent_llm._evidence_strength(_ev_src(4, 1), False) == "high"


def test_items_without_source_treated_as_verified():
    ev = [{"tool": "profile", "items": [{"pid": 1}, {"pid": 2}, {"pid": 3}]}]
    assert agent_llm._evidence_strength(ev, False) == "high"
