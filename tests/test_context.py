"""上下文（多轮指代）测试：纯逻辑部分，不依赖数据库。"""
from __future__ import annotations


def test_anaphora_detection():
    from app.context import has_anaphora
    assert has_anaphora("那这些人里谁被引最高")
    assert has_anaphora("他们谁发的论文多")
    assert has_anaphora("上面那批人里的医院")
    assert not has_anaphora("中国的呼吸科专家")
    assert not has_anaphora("介绍一下钟南山")


def test_sort_hint():
    from app.context import sort_hint
    assert sort_hint("这些人里谁被引最高") == "cites"
    assert sort_hint("他们谁论文最多") == "works"
    assert sort_hint("那这些人里谁最年轻") == "recent"
    assert sort_hint("这些人是哪个学校的") is None


def test_resolve_without_session_is_noop():
    """没有 sid 时绝不能继承任何东西（避免串会话）。"""
    from app.context import resolve
    out = resolve("那这些人里谁被引最高", "")
    assert out["inherit"] is False
    assert out["pids"] == []


def test_resolve_without_anaphora_is_noop():
    from app.context import resolve
    out = resolve("中国的呼吸科专家", "sid-x")
    assert out["inherit"] is False


def test_young_note_is_honest():
    """库里没有出生年份，回答必须说清楚，而不是假装算年龄。"""
    from app import context
    context.save("sid-test", "中国的呼吸科专家", [1, 2, 3], {})
    out = context.resolve("那这些人里谁最年轻", "sid-test")
    assert out["inherit"] is True
    assert out["sort"] == "recent"
    assert "出生年份" in out["note"]
    context.clear("sid-test")


def test_context_roundtrip():
    from app import context
    context.clear("sid-rt")
    context.save("sid-rt", "中国的呼吸科专家", [11, 22, 33], {"country": "CN"})
    prev = context.load("sid-rt")
    assert prev["pids"] == [11, 22, 33]
    assert prev["facets"]["country"] == "CN"
    out = context.resolve("这些人里谁被引最高", "sid-rt")
    assert out["pids"] == [11, 22, 33]
    assert out["sort"] == "cites"
    context.clear("sid-rt")
    assert context.load("sid-rt") is None
