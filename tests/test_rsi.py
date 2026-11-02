"""RSI 的纯函数与闸门测试（临时库，不联网、不跑对话）。

覆盖：变异白名单、合并、闸门四条规则、晋级/回滚、日志、评测集完整性。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def rsi(tmp_path, monkeypatch):
    from app import rsi as mod
    from app import user_memory
    monkeypatch.setattr(user_memory, "MEM_DB", tmp_path / "mem.db")
    monkeypatch.setattr(mod, "_ACTIVE", None, raising=False)
    monkeypatch.setattr(mod, "_ACTIVE_GEN", 0, raising=False)
    user_memory.init()
    return mod


def test_validate_rejects_unknown_kind(rsi):
    ok, why = rsi.validate({"rm_rf": 1})
    assert ok is False and "未知变异" in why


def test_validate_rejects_bad_synonyms(rsi):
    assert rsi.validate({"synonyms": {"帕金森": "parkinson"}})[0] is False
    assert rsi.validate({"synonyms": {"帕金森": []}})[0] is False
    assert rsi.validate({"synonyms": {"": ["x"]}})[0] is False
    assert rsi.validate({"synonyms": {"帕金森": ["parkinson"]}})[0] is True


def test_validate_allows_reserved_kinds(rsi):
    assert rsi.validate({"plan_prompt": "x"})[0] is True
    assert rsi.validate({"rerank_weights": {"ce": 0.5}})[0] is True


def test_merge_dedupes_and_caps(rsi):
    base = {"synonyms": {"帕金森": ["parkinson"]}}
    out = rsi.merge(base, {"synonyms": {"帕金森": ["parkinson", "pd"], "阿尔茨海默病": ["alzheimer"]}})
    assert out["synonyms"]["帕金森"] == ["parkinson", "pd"]
    assert out["synonyms"]["阿尔茨海默病"] == ["alzheimer"]
    assert base["synonyms"]["帕金森"] == ["parkinson"]      # 不就地修改父策略


def test_synonyms_for_matches_substring(rsi):
    rsi._set_active({"synonyms": {"帕金森": ["parkinson"]}}, 1)
    assert rsi.synonyms_for("帕金森专家") == ["parkinson"]
    assert rsi.synonyms_for("肺癌") == []


def test_gate_rules(rsi):
    parent, child = {}, {"synonyms": {}}
    dev_ok = {"pass_rate": 0.8}
    held_ok = {"pass_rate": 0.9}
    # 1) dev 退化
    assert rsi.gate(parent, child, 0.8, 0.5, {"pass_rate": 0.7}, held_ok, 2.0, 0.0)[0] is False
    # 2) held-out 没提升
    assert rsi.gate(parent, child, 0.8, 0.5, dev_ok, {"pass_rate": 0.51}, 2.0, 0.0)[0] is False
    # 3) 预算超限
    assert rsi.gate(parent, child, 0.8, 0.5, dev_ok, held_ok, 2.0, 3.0)[0] is False
    # 4) 通过
    ok, why = rsi.gate(parent, child, 0.8, 0.5, dev_ok, held_ok, 2.0, 0.0)
    assert ok is True and why == ""


def test_promote_and_rollback(rsi):
    p1 = {"synonyms": {"帕金森": ["parkinson"]}}
    p2 = {"synonyms": {"帕金森": ["parkinson"], "阿尔茨海默病": ["alzheimer"]}}
    rsi._set_active(p1, 1)
    rsi._set_active(p2, 2)
    assert rsi.active_gen() == 2
    out = rsi.rollback()
    assert out["ok"] is True and out["gen"] == 1
    assert rsi.active_policy()["synonyms"] == {"帕金森": ["parkinson"]}


def test_rollback_without_snapshot(rsi):
    out = rsi.rollback()
    assert out["ok"] is False
    cleared = rsi.rollback(0)
    assert cleared["ok"] is True and cleared["gen"] == 0
    assert rsi.active_policy() == {}


def test_log_and_history(rsi):
    rsi.record(1, {"synonyms": {"a": ["b"]}}, "promoted", "", 0.5, 0.6, 0.0)
    rsi.record(1, {"synonyms": {"c": ["d"]}}, "rejected", "held-out 没有提升")
    rows = rsi.history()
    assert len(rows) == 2 and rows[0]["result"] == "rejected"


def test_eval_sets_shape():
    from app.rsi import DEV, HELDOUT, PERTURBED, load_cases
    dev, held, pert = load_cases(DEV), load_cases(HELDOUT), load_cases(PERTURBED)
    assert len(dev) == 12 and len(held) == 8 and len(pert) == 8
    assert all("q" in c and "topic" in c for c in dev + held + pert)
    dev_topics = {c["topic"] for c in dev}
    assert {c["topic"] for c in held} <= dev_topics        # 问法留出，同主题池


def test_propose_rules_fallback(rsi, monkeypatch):
    from app import rsi as mod
    monkeypatch.setattr(mod.llm, "configured", lambda: False)
    cases = [{"_failed": True, "topic": "帕金森"}]
    out = mod.propose({}, cases, k=2)
    assert out and out[0]["synonyms"]["帕金森"] == ["parkinson"] or "帕金森" in out[0]["synonyms"]


def test_propose_nothing_when_no_failures(rsi):
    assert rsi.propose({}, []) == []
