"""多轮里的"简短同意"：需要/好 要被解析成上一条提议的动作，而不是当成检索词。"""
from __future__ import annotations

import pytest

from app import agent_llm, session_memory


@pytest.mark.parametrize("q", ["需要", "好", "好的", "可以", "嗯", "继续", "ok", "是"])
def test_affirmative_detected(q):
    assert agent_llm._is_affirmative(q) is True


@pytest.mark.parametrize("q", ["找研究帕金森的专家", "需要一篇综述", "强化学习相关的论文", ""])
def test_non_affirmative(q):
    assert agent_llm._is_affirmative(q) is False


def test_resolve_followup_without_history_returns_empty(monkeypatch):
    monkeypatch.setattr(session_memory, "turns", lambda sid, limit=6: [])
    assert agent_llm._resolve_followup("s-empty", "需要") == ""


def test_resolve_followup_uses_previous_assistant_message(monkeypatch):
    prev = "需要我进一步看某篇的被引脉络（它引用了谁、谁引用了它）吗？"
    monkeypatch.setattr(session_memory, "turns",
                        lambda sid, limit=6: [{"role": "user", "content": "强化学习相关的论文"},
                                              {"role": "assistant", "content": prev}])
    seen = {}

    def _fake_json(system, user, **kw):
        seen["user"] = user
        return {"ok": True, "data": {"query": "Reinforcement Learning: A Survey 的被引脉络"}}

    monkeypatch.setattr(agent_llm.llm, "chat_json", _fake_json)
    got = agent_llm._resolve_followup("s1", "需要")
    assert got == "Reinforcement Learning: A Survey 的被引脉络"
    # 上一条回复必须真的进了 prompt
    assert "被引脉络" in seen["user"]


def test_resolve_followup_rejects_still_affirmative(monkeypatch):
    monkeypatch.setattr(session_memory, "turns",
                        lambda sid, limit=6: [{"role": "assistant", "content": "要不要看引用？"}])
    monkeypatch.setattr(agent_llm.llm, "chat_json",
                        lambda system, user, **kw: {"ok": True, "data": {"query": "需要"}})
    assert agent_llm._resolve_followup("s2", "需要") == ""


# ---- 主题改写回退（模型把"考古"改写成英文长句时，用原句主题再查一次） ----
def test_find_expert_falls_back_to_original_topic(monkeypatch):
    calls = []

    def fake_kb(step):
        q = (step.get("args") or {}).get("query")
        calls.append(q)
        if q == "archaeology":          # 回退时用英文主题检索
            return {"tool": "find_expert", "count": 2,
                    "items": [{"pid": 1, "name": "A"}, {"pid": 2, "name": "B"}]}
        return {"tool": "find_expert", "count": 0, "items": []}

    monkeypatch.setattr("app.agent._run_tool", fake_kb)
    out = agent_llm._run_tool("find_expert",
                              {"query": "archaeological excavation carbon dating"},
                              "", "", orig="我想找一个考古的专家")
    assert out["count"] == 2
    assert out["query_used"] == "archaeology"
    assert out["fallback_reason"] == "empty"
    assert calls[-1] == "archaeology"


def test_no_fallback_when_first_call_has_items(monkeypatch):
    calls = []

    def fake_kb(step):
        calls.append((step.get("args") or {}).get("query"))
        return {"tool": "find_expert", "count": 1, "items": [{"pid": 9}]}

    monkeypatch.setattr("app.agent._run_tool", fake_kb)
    out = agent_llm._run_tool("find_expert", {"query": "帕金森"}, "", "",
                              orig="我想找帕金森的专家")
    assert out["count"] == 1
    assert len(calls) == 1


def test_no_fallback_when_topic_already_in_query(monkeypatch):
    calls = []

    def fake_kb(step):
        calls.append((step.get("args") or {}).get("query"))
        return {"tool": "find_expert", "count": 0, "items": []}

    monkeypatch.setattr("app.agent._run_tool", fake_kb)
    agent_llm._run_tool("find_expert", {"query": "考古 archaeology"}, "", "",
                        orig="我想找一个考古的专家")
    assert len(calls) == 1, "原主题已在查询里，不该再重试"


# ---- 解析失败时必须"回问"，不能把「好」当检索词 ----
def test_affirmative_unresolved_returns_clarify(monkeypatch):
    from app import session_memory as sm

    monkeypatch.setattr(agent_llm, "_resolve_followup", lambda sid, q: "")
    monkeypatch.setattr(sm, "turns", lambda sid, limit=6: [
        {"role": "user", "content": "强化学习相关的论文"},
        {"role": "assistant",
         "content": "需要我进一步看某篇的被引脉络（它引用了谁、谁引用了它）吗？"}])

    called = {"run": 0}

    def _boom(*a, **k):
        called["run"] += 1
        return {}

    monkeypatch.setattr(agent_llm, "run", _boom)
    r = agent_llm.chat("好", uid="", sid="s-clarify")
    assert r["finish_reason"] == "clarify"
    assert called["run"] == 0, "解析失败时绝不能把原句送进检索循环"
    assert "被引脉络" in r["answer"]          # 把上一条提议引出来
    assert r["refinements"], "应给出可点的下一步"


def test_affirmative_resolved_uses_resolved_query(monkeypatch):
    from app import session_memory as sm

    monkeypatch.setattr(agent_llm, "_resolve_followup",
                        lambda sid, q: "查一下这篇论文的引用关系")
    # 避免走到补库/记忆写入
    monkeypatch.setattr(agent_llm.episodes, "record", lambda *a, **k: None)
    monkeypatch.setattr(sm, "append", lambda *a, **k: None)
    monkeypatch.setattr(sm, "maybe_summarize", lambda *a, **k: "")
    seen = {}

    def _fake_run(query, **kw):
        seen["q"] = query
        return {"ok": True, "answer": "ok", "refused": False, "citations": [],
                "steps": [], "evidence": [], "usage": {}, "cost_usd": 0.0, "ms": 0.0,
                "engine": "llm", "memory": None, "finish_reason": "answer",
                "evidence_strength": "none"}

    monkeypatch.setattr(agent_llm, "run", _fake_run)
    agent_llm.chat("好", uid="", sid="s-resolved")
    assert seen["q"] == "查一下这篇论文的引用关系"
