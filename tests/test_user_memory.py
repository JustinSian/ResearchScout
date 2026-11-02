"""用户长期记忆（时间有效区间）的测试。

每个测试用独立的临时库（monkeypatch MEM_DB），不碰真实数据。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from app import user_memory
    monkeypatch.setattr(user_memory, "MEM_DB", tmp_path / "mem.db")
    user_memory.init()
    return user_memory


def test_add_and_read(mem):
    r = mem.add_fact("u1", "topic", "阿尔茨海默病")
    assert r["expired_previous"] == 0
    rows = mem.facts("u1")
    assert len(rows) == 1 and rows[0]["value"] == "阿尔茨海默病"


def test_new_value_expires_old_but_keeps_history(mem):
    mem.add_fact("u1", "topic", "阿尔茨海默病", at="2024-01-01T00:00:00+00:00")
    r = mem.add_fact("u1", "topic", "帕金森", at="2026-01-01T00:00:00+00:00")
    assert r["expired_previous"] == 1
    assert [f["value"] for f in mem.facts("u1")] == ["帕金森"]
    assert len(mem.facts("u1", only_active=False)) == 2


def test_as_of_returns_historical_value(mem):
    mem.add_fact("u1", "topic", "阿尔茨海默病", at="2024-01-01T00:00:00+00:00")
    mem.add_fact("u1", "topic", "帕金森", at="2026-01-01T00:00:00+00:00")
    old = [f["value"] for f in mem.facts("u1", as_of="2024-06-01T00:00:00+00:00")]
    now = [f["value"] for f in mem.facts("u1", as_of="2026-06-01T00:00:00+00:00")]
    assert old == ["阿尔茨海默病"]
    assert now == ["帕金森"]


def test_as_of_boundary_excludes_expired(mem):
    """valid_to == as_of 视为已过期（边界是开区间）。"""
    mem.add_fact("u1", "topic", "旧值", at="2024-01-01T00:00:00+00:00")
    mem.add_fact("u1", "topic", "新值", at="2025-01-01T00:00:00+00:00")
    assert mem.facts("u1", as_of="2025-01-01T00:00:00+00:00")[0]["value"] == "新值"


def test_expire_marks_inactive(mem):
    mem.add_fact("u1", "topic", "X")
    assert mem.expire("u1", "topic") == 1
    assert mem.facts("u1") == []
    assert len(mem.facts("u1", only_active=False)) == 1


def test_keys_are_normalized(mem):
    mem.add_fact("u1", "TOPIC", "X")
    mem.add_fact("u1", " topic ", "Y")
    assert [f["value"] for f in mem.facts("u1")] == ["Y"]


def test_requires_uid_key_value(mem):
    with pytest.raises(ValueError):
        mem.add_fact("", "topic", "X")
    with pytest.raises(ValueError):
        mem.add_fact("u1", "", "X")
    with pytest.raises(ValueError):
        mem.add_fact("u1", "topic", "  ")


def test_events_and_timeline(mem):
    mem.add_fact("u1", "topic", "X")
    mem.add_event("u1", "sid1", "推荐专家", "find_expert", "answered")
    tl = mem.timeline("u1")
    assert {r["kind"] for r in tl} == {"fact", "event"}
    assert mem.events("u1")[0]["query"] == "推荐专家"


def test_stats_and_clear(mem):
    mem.add_fact("u1", "topic", "X")
    mem.add_event("u1", "s", "q", "a", "o")
    s = mem.stats()
    assert s["users"] == 1 and s["facts_live"] == 1 and s["events"] == 1
    assert mem.clear("u1") == 2
    assert mem.stats()["facts_total"] == 0


def test_uid_isolation(mem):
    mem.add_fact("u1", "topic", "A")
    mem.add_fact("u2", "topic", "B")
    assert [f["value"] for f in mem.facts("u1")] == ["A"]
    assert [f["value"] for f in mem.facts("u2")] == ["B"]
