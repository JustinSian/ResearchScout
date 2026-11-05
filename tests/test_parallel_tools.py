"""多工具同轮（parallel_tool_calls）的测试。

真正要保证的是：**一轮返回的多个工具都被执行、顺序与 tool_call_id 对齐**，
且默认串行执行（避免 SQLite 抢锁），显式开启 ST_PARALLEL_EXEC=1 时才并发。
"""
from __future__ import annotations

import time

import pytest

from app import agent_llm


def _fake_round_factory():
    """第 1 次返回 3 个工具调用（模拟模型一轮并行要 3 个工具）；第 2 次不带工具。"""
    state = {"n": 0}

    def _fake(messages, tools, **kw):
        state["n"] += 1
        if state["n"] == 1:
            calls = [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": name, "arguments": "{}"}}
                for i, name in enumerate(["profile", "research_area", "works"])
            ]
            return {"ok": True, "message": {"content": "", "tool_calls": calls},
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "cost_usd": 0.0}
        return {"ok": True, "message": {"content": "结论：三位专家如下。"},
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "cost_usd": 0.0}

    return _fake


@pytest.fixture()
def patched(monkeypatch):
    monkeypatch.setattr(agent_llm.llm, "configured", lambda: True)
    monkeypatch.setattr(agent_llm.llm, "chat_with_tools", _fake_round_factory())
    monkeypatch.setattr(agent_llm.llm, "chat_text",
                        lambda messages, timeout=60.0: {"ok": True, "text": "结论：三位专家如下。",
                                                        "usage": {}, "cost_usd": 0.0})

    def _slow_tool(name, args, uid="", sid="", orig=""):
        time.sleep(0.15)
        return {"tool": name, "count": 1, "items": [{"pid": 1, "name": "X"}]}

    monkeypatch.setattr(agent_llm, "_run_tool", _slow_tool)


def test_batched_tools_all_executed_in_order(patched):
    r = agent_llm.run("找三位专家", uid="", sid="")
    assert r["ok"] is True
    assert [s["tool"] for s in r["steps"]] == ["profile", "research_area", "works"]


def test_step_callback_order_matches_tool_calls(patched):
    seen = []
    agent_llm.run("找三位专家", uid="", sid="", on_step=seen.append)
    assert [s["tool"] for s in seen] == ["profile", "research_area", "works"]


def test_default_is_serial_to_avoid_sqlite_lock(patched, monkeypatch):
    monkeypatch.delenv("ST_PARALLEL_EXEC", raising=False)
    t0 = time.perf_counter()
    agent_llm.run("找三位专家", uid="", sid="")
    assert time.perf_counter() - t0 >= 0.45, "默认应串行执行"


def test_parallel_exec_opt_in(patched, monkeypatch):
    monkeypatch.setenv("ST_PARALLEL_EXEC", "1")
    t0 = time.perf_counter()
    agent_llm.run("找三位专家", uid="", sid="")
    assert time.perf_counter() - t0 < 0.40, "开启后应并发执行"
