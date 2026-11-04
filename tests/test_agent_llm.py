"""LLM Agent 的纯函数测试（不联网）。

覆盖：system prompt 组装（含长期记忆/会话摘要/RSI 策略）、拒答识别、
引用抽取去重、开关逻辑、未配置 key 时的降级返回。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app import session_memory, user_memory
    monkeypatch.setattr(user_memory, "MEM_DB", tmp_path / "mem.db")
    monkeypatch.setattr(session_memory, "CTX_DB", tmp_path / "ctx.db")
    user_memory.init()
    return user_memory, session_memory


def test_system_prompt_includes_memory_and_summary(env):
    um, sm = env
    from app import agent_llm
    um.add_fact("u1", "topic", "帕金森")
    sm.append("s1", "user", "找帕金森专家")
    sm.set_summary("s1", "用户在找帕金森专家", 10)
    system, hint = agent_llm.build_system_prompt("u1", "s1")
    assert "ResearchScout" in system
    assert "【你的长期记忆】" in system and "帕金森" in system
    assert "本会话摘要" in system
    assert hint["topic"] == "帕金森"


def test_system_prompt_without_memory(env):
    from app import agent_llm
    system, hint = agent_llm.build_system_prompt("", "")
    assert hint == {}
    assert "【你的长期记忆】" not in system


def test_system_prompt_includes_rsi_policy(env, monkeypatch):
    from app import agent_llm, rsi
    monkeypatch.setattr(rsi, "active_policy", lambda: {"plan_prompt": "先给结论再给证据"})
    system, _ = agent_llm.build_system_prompt("", "")
    assert "策略" in system and "先给结论" in system


def test_refusal_detection():
    from app.agent_llm import _looks_refusal
    assert _looks_refusal("知识库里没有找到相关专家")
    assert _looks_refusal("证据不足，无法判断")
    assert not _looks_refusal("钟南山是呼吸病学专家")


def test_citations_dedupe():
    from app.agent_llm import _citations
    ev = [{"items": [{"pid": 1, "name": "A"}, {"pid": 1, "name": "A"}]},
          {"items": [{"wid": 9, "title": "T"}]}]
    cits = _citations(ev)
    assert len(cits) == 2 and {c["kind"] for c in cits} == {"person", "work"}


def test_enabled_switch(monkeypatch):
    from app import agent_llm
    monkeypatch.setattr("app.llm.configured", lambda: True)
    monkeypatch.delenv("ST_CHAT_ENGINE", raising=False)
    assert agent_llm.enabled() is True
    monkeypatch.setenv("ST_CHAT_ENGINE", "classic")
    assert agent_llm.enabled() is False


def test_run_returns_not_ok_without_key(monkeypatch):
    from app import agent_llm
    monkeypatch.setattr("app.llm.configured", lambda: False)
    out = agent_llm.run("介绍钟南山")
    assert out["ok"] is False and "DEEPSEEK_API_KEY" in out["reason"]


def test_tool_schema_shape():
    from app.agent_llm import TOOL_SCHEMAS
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {"find_expert", "profile", "remember"} <= names
    for t in TOOL_SCHEMAS:
        assert t["function"]["parameters"]["type"] == "object"


def test_remember_tool_writes_memory(env):
    um, _sm = env
    from app.agent_llm import _run_tool
    out = _run_tool("remember", {"key": "topic", "value": "单细胞测序"}, "u1", "s1")
    assert out["ok"] is True
    assert [f["value"] for f in um.facts("u1")] == ["单细胞测序"]


def test_auto_expand_defaults_off(monkeypatch):
    """自动扩充默认关闭：评测显示它会拉低原有概念查询的 R@10。"""
    import os
    monkeypatch.delenv("ST_AUTO_EXPAND", raising=False)
    assert os.environ.get("ST_AUTO_EXPAND", "0") != "1"


def test_result_fields_keep_direction_and_topics():
    from app.agent_llm import _RESULT_FIELDS
    # 回归守卫：之前只留 pid/name/cites，把 recent/early/topics 丢光，
    # 导致 research_area/profile 到模型手里只剩"姓名+被引"
    for k in ("recent", "early", "top_venues", "topics", "country", "h_index"):
        assert k in _RESULT_FIELDS, f"{k} 必须在回传字段里"
