"""澄清增强：空问句要给出可点的具体方向，而不是只反问一句。"""
from __future__ import annotations

from app import agent, agent_llm


def test_top_topics_skips_short_single_words(monkeypatch):
    rows = [
        {"topic": "Gene", "n": 500},
        {"topic": "Lung cancer", "n": 40},
        {"topic": "Medicine", "n": 900},          # 泛学科词应被过滤
        {"topic": "Adenosine receptor", "n": 12},
    ]
    monkeypatch.setattr(agent, "_TOP_CACHE", {"ts": 0.0, "items": []})

    class _Cur:
        def fetchall(self):
            return rows

    class _C:
        def execute(self, *a, **k):
            return _Cur()

        def close(self):
            pass

    monkeypatch.setattr(agent, "connect", lambda: _C())
    items = agent.top_topics(6)
    assert "Medicine" not in items
    assert "Gene" not in items                 # 短单词被过滤
    assert "Lung cancer" in items
    assert "Adenosine receptor" in items
    assert items.index("Lung cancer") < items.index("Adenosine receptor")


def test_clarify_returns_clickable_refinements(monkeypatch):
    monkeypatch.setattr(agent, "top_topics", lambda n=6: ["Lung cancer", "CRISPR base editing"])
    r = agent_llm._clarify("推荐几个专家", "")
    assert r["finish_reason"] == "clarify"
    labels = [x["label"] for x in r["refinements"]]
    assert labels == ["Lung cancer", "CRISPR base editing"]
    assert all(x["query"].startswith("找研究") for x in r["refinements"])


def test_clarify_not_triggered_for_real_question():
    assert agent_llm._clarify("找研究帕金森的专家", "") is None


def test_clarify_falls_back_when_no_topics(monkeypatch):
    monkeypatch.setattr(agent, "top_topics", lambda n=6: [])
    r = agent_llm._clarify("推荐几个专家", "")
    assert r["refinements"] == []
    assert "告诉我方向" in r["answer"]
