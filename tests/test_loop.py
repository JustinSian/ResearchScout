"""LLM 工具循环的纯函数测试（不联网）。

循环本身要真实调用模型才能测，所以这里只测**不依赖网络的部分**：
工具 schema 与映射的一致性、结果截断、引用抽取、开关逻辑。
"""
from __future__ import annotations


def test_tool_schemas_are_valid():
    from app.loop_llm import TOOL_SCHEMAS
    assert len(TOOL_SCHEMAS) >= 6
    for t in TOOL_SCHEMAS:
        fn = t["function"]
        assert t["type"] == "function"
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert fn["parameters"]["properties"]
        for req in fn["parameters"].get("required", []):
            assert req in fn["parameters"]["properties"]


def test_every_schema_maps_to_call():
    """schema 里的每个工具都必须能被 to_call 映射，否则模型一调就报错。"""
    from app.loop_llm import TOOL_SCHEMAS
    from app.plan_llm import to_call
    sample = {"query": "x", "name": "x", "a": "x", "b": "y", "limit": 3, "hops": 1}
    for t in TOOL_SCHEMAS:
        name = t["function"]["name"]
        out = to_call({"tool": name, "args": sample}, "x")
        assert out is not None, name
        assert out["tool"] == name


def test_truncate_keeps_short_and_cuts_long():
    from app.loop_llm import _truncate
    assert _truncate({"a": 1}) == '{"a": 1}'
    long = {"items": list(range(500))}
    out = _truncate(long, limit=100)
    assert len(out) <= 120 and out.endswith("(截断)")


def test_citations_dedup():
    from app.loop_llm import _citations_from
    ev = [{"items": [{"pid": 1, "name": "A"}, {"pid": 1, "name": "A"}]},
          {"items": [{"wid": 9, "title": "T"}]}]
    cits = _citations_from(ev)
    assert len(cits) == 2
    assert {c["kind"] for c in cits} == {"person", "work"}


def test_enabled_switch(monkeypatch):
    from app import loop_llm
    monkeypatch.delenv("ST_AGENT_LOOP", raising=False)
    assert loop_llm.enabled() is False
    monkeypatch.setenv("ST_AGENT_LOOP", "1")
    assert loop_llm.enabled() is True


def test_run_returns_none_without_key(monkeypatch):
    from app import loop_llm
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(loop_llm.llm, "configured", lambda: False)
    assert loop_llm.run("介绍一下钟南山") is None
