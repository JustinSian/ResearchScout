"""取消与超时：用户点"停止"或超过墙钟上限时，Agent 必须立刻收手。"""
from __future__ import annotations

import pytest

from app import agent_llm


@pytest.fixture()
def no_llm_calls(monkeypatch):
    """任何一次 LLM 调用都算失败（用来证明"根本没发请求"）。"""
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("不应该调用 LLM")

    monkeypatch.setattr(agent_llm.llm, "configured", lambda: True)
    monkeypatch.setattr(agent_llm.llm, "chat_with_tools", _boom)
    monkeypatch.setattr(agent_llm.llm, "chat_text", _boom)
    return called


def test_cancel_short_circuits_before_any_llm_call(no_llm_calls):
    r = agent_llm.run("介绍一下钟南山", uid="", sid="", on_should_stop=lambda: True)
    assert r["ok"] is True
    assert r["finish_reason"] == "cancelled"
    assert r["answer"].startswith("已停止")
    assert no_llm_calls["n"] == 0


def test_deadline_aborts_immediately(no_llm_calls):
    r = agent_llm.run("介绍一下钟南山", uid="", sid="", deadline_s=0)
    assert r["ok"] is True
    assert r["finish_reason"] == "timeout"
    assert "秒已中止" in r["answer"]
    assert no_llm_calls["n"] == 0


def test_chat_passes_cancel_through(no_llm_calls):
    r = agent_llm.chat("介绍一下钟南山", uid="", sid="", on_should_stop=lambda: True)
    assert r.get("finish_reason") == "cancelled"
    assert no_llm_calls["n"] == 0
