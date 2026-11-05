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
