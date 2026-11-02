"""LLM 规划器的纯函数测试（不联网、不需要 key）。

覆盖三件容易出错的事：
1. `.env` 解析（不覆盖已有环境变量）；
2. 模型输出的**校验与修补**（工具白名单、参数白名单、类型收敛）；
3. plan → 真实工具签名的映射（避免 TypeError）。
"""
from __future__ import annotations


def test_load_env_does_not_override(tmp_path, monkeypatch):
    from app import llm
    f = tmp_path / ".env"
    f.write_text("# 注释\nFOO_A=1\nFOO_B='two'\n\n", encoding="utf-8")
    monkeypatch.setenv("FOO_A", "keep")
    monkeypatch.delenv("FOO_B", raising=False)
    llm.load_env(f)
    import os
    assert os.environ["FOO_A"] == "keep"      # 不覆盖
    assert os.environ["FOO_B"] == "two"       # 去引号


def test_strip_fence():
    from app.llm import _strip_fence
    assert _strip_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_fence('{"a":1}') == '{"a":1}'


def test_cost_formula():
    from app.llm import cost_usd
    assert cost_usd(0, 0) == 0
    assert cost_usd(1_000_000, 0) > 0
    assert cost_usd(0, 1_000_000) > cost_usd(1_000_000, 0)   # 输出更贵


def test_parse_plan_rejects_unknown_tool():
    from app.plan_llm import parse_plan
    assert parse_plan('{"tool": "rm_rf", "args": {}}') is None
    assert parse_plan("not json") is None
    assert parse_plan("[]") is None


def test_parse_plan_strips_unknown_args_and_coerces():
    from app.plan_llm import parse_plan
    got = parse_plan('{"tool":"works","args":{"query":"BERT","bogus":1,"limit":"3"}}')
    assert got == {"tool": "works", "args": {"query": "BERT", "limit": 3}}


def test_parse_plan_clamps_hops():
    from app.plan_llm import parse_plan
    assert parse_plan('{"tool":"citations","args":{"hops":9}}')["args"]["hops"] == 2
    assert parse_plan('{"tool":"citations","args":{"hops":1}}')["args"]["hops"] == 1


def test_to_call_find_expert_composes_query():
    from app.plan_llm import to_call
    out = to_call({"tool": "find_expert",
                   "args": {"country": "CN", "inst_kind": "hospital",
                            "topic": "respiratory", "sort": "cites"}}, "原始问题")
    q = out["args"]["query"]
    assert out["tool"] == "find_expert"
    assert "中国" in q and "医院" in q and "被引最多" in q and "respiratory" in q


def test_to_call_compare_requires_two_names():
    from app.plan_llm import to_call
    assert to_call({"tool": "compare", "args": {"a": "钟南山"}}, "对比") is None
    out = to_call({"tool": "compare", "args": {"a": "钟南山", "b": "李兰娟"}}, "对比")
    assert out["args"]["names"] == ["钟南山", "李兰娟"]


def test_to_call_task_builds_query():
    from app.plan_llm import to_call
    out = to_call({"tool": "task", "args": {"intent": "reviewer-match",
                                            "topic": "lung cancer"}}, "评审")
    assert out["tool"] == "task" and "lung cancer" in out["args"]["query"]


def test_to_call_handles_all_tools():
    from app.plan_llm import TOOLS, to_call
    for tool in TOOLS:
        args = {"name": "x", "query": "x", "a": "x", "b": "y", "intent": "i"}
        out = to_call({"tool": tool, "args": args}, "x")
        assert out is not None and out["tool"] == tool


def test_enabled_requires_mode_and_key(monkeypatch):
    from app import llm, plan_llm
    monkeypatch.setenv("ST_PLANNER", "rules")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    assert plan_llm.enabled() is False
    monkeypatch.setenv("ST_PLANNER", "llm")
    assert plan_llm.enabled() is True
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert plan_llm.enabled() is False
