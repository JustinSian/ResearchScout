"""补库公示板 + 姓名精确匹配的测试。"""
from __future__ import annotations

import pytest

from app.agent_llm import _expansion_message


def test_message_for_successful_expansion():
    msg = _expansion_message({"topic": "CRISPR base editing", "attempted": True,
                              "ok": True, "added_people": 2, "added_works": 24,
                              "answered_after_retry": True})
    assert "CRISPR base editing" in msg and "2 位学者" in msg and "24 篇" in msg
    assert "重试" in msg


def test_message_when_nothing_added():
    msg = _expansion_message({"topic": "X", "attempted": True, "ok": True,
                              "added_people": 0, "added_works": 0})
    assert "未入库" in msg


def test_message_for_24h_lock():
    msg = _expansion_message({"topic": "Y", "attempted": True, "ok": False,
                              "reason": "24 小时内已扩充过"})
    assert "24 小时" in msg


def test_message_for_failure():
    msg = _expansion_message({"topic": "Z", "attempted": True, "ok": False,
                              "reason": "OpenAlex 失败"})
    assert "失败" in msg


def test_message_when_disabled():
    msg = _expansion_message({"topic": "W", "attempted": False})
    assert "未执行" in msg


def test_resolve_person_prefers_exact_name():
    """精确姓名优先，避免 FTS 分词把 'Alexis C. Komor' 判成 'Alexis J. Battle'。"""
    from app.agent import _resolve_person
    from app.db import connect
    try:
        n = connect().execute("SELECT COUNT(*) FROM people").fetchone()[0]
    except Exception:
        n = 0
    if not n:
        pytest.skip("no database")
    p = _resolve_person(None, "Nanshan Zhong")
    assert p is not None and p["pid"] == 527
    assert _resolve_person(None, "名字根本不存在的人XYZ") is None
