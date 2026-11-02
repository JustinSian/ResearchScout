"""记忆抽取（规则）与召回的测试。

抽取是纯函数；抽取写库与召回用临时库。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from app import user_memory
    monkeypatch.setattr(user_memory, "MEM_DB", tmp_path / "mem.db")
    user_memory.init()
    return user_memory


# ---------------- extraction ----------------

def test_extract_topic():
    from app.memory_extract import extract
    out = extract("我现在主要做阿尔茨海默病早期诊断")
    assert out == [{"op": "set", "key": "topic", "value": "阿尔茨海默病早期诊断"}]


def test_extract_field():
    from app.memory_extract import extract
    out = extract("我的研究方向是肠道菌群")
    assert out[0]["key"] == "field" and out[0]["value"] == "肠道菌群"


def test_extract_only():
    from app.memory_extract import extract
    out = extract("我现在只做帕金森")
    assert out == [{"op": "set", "key": "topic", "value": "帕金森"}]


def test_extract_redirect_expires_then_sets():
    from app.memory_extract import extract
    out = extract("我现在不做阿尔茨海默病了，转做帕金森")
    assert out[0] == {"op": "expire", "key": "topic", "value": "阿尔茨海默病"}
    assert out[1] == {"op": "set", "key": "topic", "value": "帕金森"}


def test_extract_constraint_and_preference():
    from app.memory_extract import extract
    assert extract("我不要临床数据")[0]["key"] == "constraint"
    assert extract("我更喜欢理论扎实的论文")[0]["key"] == "preference"


def test_extract_english_field():
    from app.memory_extract import extract
    out = extract("I work on protein structure prediction")
    assert out[0]["key"] == "field"
    assert "protein structure" in out[0]["value"]


def test_extract_ignores_plain_question():
    from app.memory_extract import extract
    assert extract("找研究 dementia 的专家") == []
    assert extract("") == []


def test_extract_llm_rejects_bad_payload(monkeypatch):
    from app import memory_extract
    monkeypatch.setattr("app.llm.configured", lambda: True)
    monkeypatch.setattr("app.llm.chat_json",
                        lambda *a, **k: {"ok": True, "data": {"facts": [
                            {"op": "set", "key": "rm_rf", "value": "x"},
                            {"op": "set", "key": "topic", "value": "帕金森"}]}})
    out = memory_extract.extract_llm("随便")
    assert out == [{"op": "set", "key": "topic", "value": "帕金森"}]


def test_extract_llm_failure_returns_empty(monkeypatch):
    from app import memory_extract
    monkeypatch.setattr("app.llm.configured", lambda: True)
    monkeypatch.setattr("app.llm.chat_json", lambda *a, **k: {"ok": False})
    assert memory_extract.extract_llm("随便") == []


def test_apply_writes_and_reports(mem):
    from app import memory_extract
    r = memory_extract.apply("u1", "我研究肺癌免疫治疗")
    assert r["added"] == 1 and r["source"] == "rule"
    assert [f["value"] for f in mem.facts("u1")] == ["肺癌免疫治疗"]


def test_apply_without_uid_does_nothing(mem):
    from app import memory_extract
    r = memory_extract.apply("", "我研究肺癌免疫治疗")
    assert r["added"] == 0 and r["source"] == "no_uid"
    assert mem.stats()["facts_total"] == 0


def test_apply_redirect_expires_previous(mem):
    from app import memory_extract
    memory_extract.apply("u1", "我研究阿尔茨海默病")
    r = memory_extract.apply("u1", "我现在不做阿尔茨海默病了，转做帕金森")
    assert r["added"] == 1 and r["expired"] >= 1
    assert [f["value"] for f in mem.facts("u1")] == ["帕金森"]


# ---------------- recall ----------------

def test_recall_ranks_relevant_first(mem):
    from app import memory_recall
    mem.add_fact("u1", "topic", "帕金森")
    mem.add_fact("u1", "constraint", "只要医院的")
    out = memory_recall.recall("u1", "推荐帕金森专家")
    assert out[0]["value"] == "帕金森"


def test_recall_empty_without_uid_or_facts(mem):
    from app import memory_recall
    assert memory_recall.recall("", "随便") == []
    assert memory_recall.recall("u-none", "随便") == []


def test_build_hint_picks_topic_and_note(mem):
    from app import memory_recall
    mem.add_fact("u1", "topic", "帕金森")
    h = memory_recall.build_hint("u1", "推荐几个专家")
    assert h["topic"] == "帕金森"
    assert "长期记忆" in h["note"]
    assert h["facts"]


def test_build_hint_empty_when_no_facts(mem):
    from app import memory_recall
    assert memory_recall.build_hint("u1", "推荐几个专家") == {}


def test_apply_to_parsed_fills_only_empty(monkeypatch):
    from app import memory_recall
    monkeypatch.setattr("app.facets.topic_en", lambda t, max_terms=3: [t])
    hint = {"topic": "帕金森"}
    filled = memory_recall.apply_to_parsed({"topic": ""}, hint)
    assert filled["topic"] == "帕金森" and "topic" in filled["memory_fills"]

    kept = memory_recall.apply_to_parsed({"topic": "阿尔茨海默病"}, hint)
    assert kept["topic"] == "阿尔茨海默病"
    assert "topic" in kept["explicit_keeps"]


def test_note_text(mem):
    from app import memory_recall
    mem.add_fact("u1", "topic", "帕金森")
    h = memory_recall.build_hint("u1", "推荐专家")
    assert memory_recall.note(h).startswith("（已按你的长期记忆：方向=帕金森")


def test_log_event_is_safe_without_uid(mem):
    from app import memory_recall
    memory_recall.log_event("", "s", "q", {})          # 不应抛异常
    memory_recall.log_event("u1", "s", "q", {"refused": True, "route": "kb"})
    assert mem.events("u1")[0]["outcome"] == "refused"
