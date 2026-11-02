"""短期记忆与会话摘要的测试（临时库）。"""
from __future__ import annotations

import pytest


@pytest.fixture()
def sm(tmp_path, monkeypatch):
    from app import session_memory
    monkeypatch.setattr(session_memory, "CTX_DB", tmp_path / "ctx.db")
    return session_memory


def test_append_and_read_back(sm):
    sm.append("s1", "user", "找帕金森专家")
    sm.append("s1", "assistant", "推荐 5 位")
    rows = sm.turns("s1")
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert sm.count("s1") == 2


def test_turns_are_windowed(sm):
    for i in range(12):
        sm.append("s1", "user", f"q{i}")
    assert len(sm.turns("s1", limit=4)) == 4
    assert sm.turns("s1", limit=4)[-1]["content"] == "q11"


def test_bounded_turns(sm, monkeypatch):
    monkeypatch.setattr(sm, "MAX_TURNS_KEEP", 5)
    for i in range(9):
        sm.append("s1", "user", f"q{i}")
    assert sm.count("s1") == 5


def test_blank_input_ignored(sm):
    sm.append("", "user", "x")
    sm.append("s1", "user", "   ")
    assert sm.count("s1") == 0


def test_summary_roundtrip(sm):
    sm.set_summary("s1", "用户在找帕金森专家", 12)
    assert "帕金森" in sm.summary("s1")


def test_prompt_block_contains_summary_and_tail(sm):
    sm.append("s1", "user", "找帕金森专家")
    sm.set_summary("s1", "用户在找帕金森专家", 10)
    block = sm.prompt_block("s1")
    assert "本会话摘要" in block and "帕金森" in block
    assert "最近几轮" in block


def test_prompt_block_empty_without_history(sm):
    assert sm.prompt_block("nobody") == ""


def test_reset_clears_both(sm):
    sm.append("s1", "user", "x")
    sm.set_summary("s1", "y", 3)
    sm.reset("s1")
    assert sm.count("s1") == 0 and sm.summary("s1") == ""


def test_maybe_summarize_below_threshold_keeps_empty(sm):
    sm.append("s1", "user", "x")
    assert sm.maybe_summarize("s1") == ""


def test_maybe_summarize_falls_back_without_llm(sm, monkeypatch):
    monkeypatch.setattr(sm, "SUMMARIZE_AFTER", 2)
    monkeypatch.setattr("app.llm.configured", lambda: False)
    sm.append("s1", "user", "找帕金森专家")
    sm.append("s1", "assistant", "推荐 5 位")
    out = sm.maybe_summarize("s1")
    assert "帕金森" in out
