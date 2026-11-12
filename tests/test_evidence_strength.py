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
    items = [{"pid": i + 1} for i in range(n_verified)]
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


# ---- 比例只在人物条目里算：论文不能稀释 ----
def test_works_do_not_dilute_person_ratio():
    ev = [{"tool": "find_expert", "items": [
              {"pid": 1, "source": "auto_expand"},
              {"pid": 2, "source": "auto_expand"},
              {"pid": 3}]},
          {"tool": "works", "items": [{"wid": i} for i in range(5)]}]
    # 人物里 2/3 是补库 → medium；论文条目没有 source，不该把分母冲大
    assert agent_llm._evidence_strength(ev, False) == "medium"


# ---- 严格"超过一半"才算多数：2 核验 + 2 补库仍是 high ----
def test_half_auto_is_not_majority():
    assert agent_llm._evidence_strength(_ev_src(2, 2), False) == "high"


# ---- 契约测试：真实工具必须真的带 source 字段 ----
def test_source_survives_result_fields():
    assert "source" in agent_llm._RESULT_FIELDS, \
        "source 被移出 _RESULT_FIELDS → 证据里的来源会被裁掉，降级规则失效"


def test_find_expert_items_carry_source():
    from app import agent
    r = agent.tool_find_expert(query="cancer", limit=3)
    items = r.get("items") or []
    assert items, "demo 库应能查到 cancer 相关专家"
    assert all("source" in it for it in items), \
        f"find_expert 返回缺 source 字段: {sorted(items[0].keys())}"
