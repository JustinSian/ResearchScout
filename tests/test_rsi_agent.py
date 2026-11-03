"""Agent 自我进化的纯函数测试（不联网、不跑 LLM）。

覆盖：策略补丁的校验、失败轨迹的构造、闸门规则、评测集切分。
"""
from __future__ import annotations

import pytest


def test_agent_eval_sets_are_disjoint():
    from app.rsi_agent import DEV, HELDOUT, load_cases
    dev = {c["id"] for c in load_cases(DEV)}
    held = {c["id"] for c in load_cases(HELDOUT)}
    assert dev and held
    assert not (dev & held), "dev 与 held-out 不能重叠"


def test_validate_prompt_accepts_reasonable_text():
    from app.rsi_agent import validate_prompt
    ok, _ = validate_prompt({"plan_prompt": "涉及人名时先调用 profile；证据为空时直接说没有找到。"})
    assert ok is True


def test_validate_prompt_rejects_bad_input():
    from app.rsi_agent import validate_prompt
    assert validate_prompt({"plan_prompt": "短"})[0] is False
    assert validate_prompt({"plan_prompt": "x" * 400})[0] is False
    assert validate_prompt({"plan_prompt": "```json\n{}\n```"})[0] is False
    assert validate_prompt({"synonyms": {"a": ["b"]}})[0] is False
    assert validate_prompt({})[0] is False


def test_propose_returns_empty_without_llm(monkeypatch):
    from app import rsi_agent
    monkeypatch.setattr(rsi_agent.llm, "configured", lambda: False)
    assert rsi_agent.propose_prompt([{"q": "x", "failed_checks": ["must_any_ok"]}]) == []


def test_propose_parses_and_dedupes(monkeypatch):
    from app import rsi_agent
    monkeypatch.setattr(rsi_agent.llm, "configured", lambda: True)
    text = "涉及人名时先调用 profile 工具；拿不到证据就直接说没有找到。"
    monkeypatch.setattr(rsi_agent.llm, "chat_json",
                        lambda *a, **k: {"ok": True, "data": {"plan_prompt": text}})
    out = rsi_agent.propose_prompt([{"q": "介绍钟南山", "failed_checks": ["tools_ok"]}])
    assert len(out) == 1 and out[0]["plan_prompt"] == text


def test_propose_skips_invalid_payload(monkeypatch):
    from app import rsi_agent
    monkeypatch.setattr(rsi_agent.llm, "configured", lambda: True)
    monkeypatch.setattr(rsi_agent.llm, "chat_json",
                        lambda *a, **k: {"ok": True, "data": {"plan_prompt": "短"}})
    assert rsi_agent.propose_prompt([{"q": "x"}]) == []


def test_gate_agent_rules():
    from app.rsi_agent import gate_agent
    dev = {"pass_rate": 0.8}
    held = {"pass_rate": 0.8}
    # dev 没提升
    assert gate_agent(0.8, 0.8, {"pass_rate": 0.8}, held, 0.002, 1.0)[0] is False
    # held 退步
    assert gate_agent(0.5, 0.8, dev, {"pass_rate": 0.5}, 0.002, 1.0)[0] is False
    # 缺 held
    assert gate_agent(0.5, 0.8, dev, None, 0.002, 1.0)[0] is False
    # 成本过高
    assert gate_agent(0.5, 0.8, dev, held, 0.05, 1.0)[0] is False
    # 通过
    ok, why = gate_agent(0.5, 0.5, dev, held, 0.002, 1.0)
    assert ok is True and why == ""


def test_evaluate_agent_scores_without_network(monkeypatch):
    from app import rsi_agent
    calls = []

    def fake_chat(q, uid="", sid=""):
        calls.append(q)
        return {"ok": True, "answer": "钟南山是呼吸专家", "refused": False,
                "citations": [{"kind": "person", "id": 1}], "steps": [{"tool": "profile"}],
                "cost_usd": 0.001, "memory": None}

    monkeypatch.setattr(rsi_agent.agent_llm, "chat", fake_chat)
    cases = [{"id": "x", "q": "介绍钟南山",
              "expect": {"must_any": ["钟南山"], "tools_any": ["profile"]}}]
    out = rsi_agent.evaluate_agent({}, cases, log=lambda *_: None)
    assert out["pass_rate"] == 1.0 and out["n"] == 1
    assert calls == ["介绍钟南山"]
