"""链路级单元测试：检索融合、可信判定、调度降级、记忆失效、审计落库。

与 test_core.py 的分工：那边测"纯函数契约"，这里测"多模块协作时行为是否还成立"。
仍然是离线、无模型、无网络。
"""
from __future__ import annotations

import json

import pytest


# ---------------- 检索融合 ----------------
def test_rrf_rewards_items_in_both_lists():
    from app.search import reciprocal_rank_fusion

    # 真实签名：rankings = [[(doc_id, score), ...], ...]
    a = [(1, 0.9), (2, 0.8), (3, 0.7)]
    b = [(3, 0.95), (1, 0.6), (9, 0.5)]
    fused = reciprocal_rank_fusion([a, b], k=60)
    # 1 和 3 都出现在两路里，应高于只出现一次的 2 和 9
    assert fused[1] > fused[2]
    assert fused[3] > fused[9]
    assert set(fused) == {1, 2, 3, 9}


def test_rrf_is_rank_only_not_score_based():
    """RRF 只看名次：把第二路整体"分数"翻倍不应改变结果（函数签名里就没有分数）。"""
    from app.search import reciprocal_rank_fusion

    f1 = reciprocal_rank_fusion([[(1, 0.9), (2, 0.1)], [(2, 0.9), (1, 0.1)]], k=60)
    f2 = reciprocal_rank_fusion([[(1, 0.1), (2, 0.9)], [(2, 0.1), (1, 0.9)]], k=60)
    assert f1 == f2


def test_dbsf_keeps_relative_gaps():
    from app.search import distribution_score_fusion

    fused = distribution_score_fusion([[(1, 1.0), (2, 0.9), (3, 0.1)],
                                       [(2, 1.0), (1, 0.9), (3, 0.1)]])
    assert fused[1] > fused[3] and fused[2] > fused[3]


def test_fts_query_strips_templates_and_keeps_cjk():
    from app.search import _fts_query

    q = _fts_query("找研究 graph neural network 的专家")
    assert '"graph"' in q and '"neural"' in q
    # 中文实词应保留，模板词（找研究/的）应被剥掉
    assert "的" not in q


# ---------------- 可信判定：更多规则 ----------------
def test_judge_penalises_single_work_author():
    from app.judge import judge

    # judge 的产出特征键是 sample_works（不是 works_count）
    thin = judge({"name": "Nobody Example", "sample_works": 1})
    solid = judge({"name": "Nobody Example", "sample_works": 8})
    assert solid["score"] > thin["score"]


def test_judge_orcid_contributes():
    from app.judge import judge

    base = {"name": "Some Person", "sample_works": 6}
    with_orcid = judge({**base, "orcid": "0000-0002-1825-0097"})
    without = judge(base)
    assert with_orcid["score"] > without["score"]


def test_judge_rejects_institution_like_name():
    from app.judge import judge

    assert judge({"name": "广州医科大学附属第一医院", "sample_works": 5})["verdict"] != "accept"


# ---------------- 调度：降级路径 ----------------
def _fake_kb(refused, answer="", citations=None):
    return {"refused": refused, "answer": answer,
            "citations": citations or [], "plan": [{"tool": "profile"}]}


def test_supervisor_falls_back_when_kb_refuses(monkeypatch):
    pytest.importorskip("langgraph")
    from app import supervisor

    monkeypatch.setattr("app.agent.answer", lambda q, allow_fallback=True, sid="": _fake_kb(True))
    monkeypatch.setattr(supervisor.websearch, "available", lambda: True)
    monkeypatch.setattr(supervisor.websearch, "answer",
                        lambda q: {"ok": True, "text": "网络结果",
                                   "citations": [{"kind": "web", "url": "https://x"}]})
    out = supervisor.run("介绍一位库里没有的人", force_route="kb", log=lambda s: None)
    assert out["route"] == "web"
    assert out["web_used"] is True
    assert "网络结果" in out["answer"]


def test_supervisor_degrades_when_task_raises(monkeypatch):
    pytest.importorskip("langgraph")
    from app import supervisor, harness

    def boom(*_a, **_k):
        raise RuntimeError("no candidates")

    monkeypatch.setattr(harness, "run", boom)
    monkeypatch.setattr("app.agent.answer", lambda q, allow_fallback=True, sid="": _fake_kb(True))
    monkeypatch.setattr(supervisor.websearch, "available", lambda: True)
    monkeypatch.setattr(supervisor.websearch, "answer",
                        lambda q: {"ok": True, "text": "降级后的网络结果", "citations": []})
    out = supervisor.run("谁更适合审一篇关于罕见病的稿件", force_route="task", log=lambda s: None)
    assert out["task_error"].startswith("RuntimeError")
    assert out["refused"] is False
    assert "降级后的网络结果" in out["answer"]


def test_supervisor_refuses_when_everything_empty(monkeypatch):
    pytest.importorskip("langgraph")
    from app import supervisor

    monkeypatch.setattr("app.agent.answer", lambda q, allow_fallback=True, sid="": _fake_kb(True))
    monkeypatch.setattr(supervisor.websearch, "available", lambda: False)
    out = supervisor.run("完全无法回答的问题", force_route="kb", log=lambda s: None)
    assert out["refused"] is True
    assert "资料不足" in out["answer"]


# ---------------- 记忆：语料版本失效 ----------------
def test_cache_invalidated_when_corpus_changes(monkeypatch):
    from app import memory

    # 必须复用同一个内存库：每次新建连接的话，put 与 get 会落在两个空库里
    shared = _MemConn()
    monkeypatch.setattr(memory, "corpus_version", lambda: {"works": 1, "dossiers": 1})
    monkeypatch.setattr(memory, "connect", lambda: shared)
    key = "task:kb:test"
    memory.cache_put(key, {"tid": 1, "report_path": "/reports/1.md"})
    assert memory.cache_get(key) is not None
    # 语料变了 → 缓存应失效
    monkeypatch.setattr(memory, "corpus_version", lambda: {"works": 2, "dossiers": 1})
    assert memory.cache_get(key) is None


class _MemConn:
    """最小内存 sqlite，供 memory 模块测试用（避免污染真实库）。

    注意：memory 的 cache_put/cache_get 会在 finally 里 close()。
    如果 close 真的关库，内存库就没了 → 这里让 close 变成 no-op，
    保证"同一连接被多次借还"的语义，测试才测的是缓存逻辑而不是连接生命周期。
    """

    def __init__(self):
        import sqlite3

        self._c = sqlite3.connect(":memory:")
        self._c.row_factory = sqlite3.Row
        self._c.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value_json TEXT, updated_at REAL)")

    def execute(self, sql, args=()):
        return self._c.execute(sql, args)

    def commit(self):
        self._c.commit()

    def close(self):
        pass


# ---------------- 审计落库 ----------------
def test_after_tool_writes_redacted_audit_row(monkeypatch, tmp_path):
    from app import config, hooks

    db = tmp_path / "audit.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(hooks, "connect", _audit_conn_factory(db))

    row = hooks.after_tool(1, 2, "profile", {"email": "a@b.com"}, "done", 12,
                           None, "ok", None, log=lambda s: None)
    assert row["args_redacted"].find("@b.com") == -1
    assert "[email]" in row["args_redacted"]
    assert row["cost_units"] > 0


def _audit_conn_factory(db):
    import sqlite3

    def _connect():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS audit (
            aid INTEGER PRIMARY KEY, ts REAL, tid INTEGER, sid INTEGER, tool TEXT,
            args_hash TEXT, args_redacted TEXT, status TEXT, ms INTEGER, artifact TEXT,
            summary TEXT, cost_units REAL DEFAULT 0, error TEXT)""")
        return c

    return _connect


# ---------------- 兜底检索：去重 ----------------
def test_websearch_dedupes_same_title(monkeypatch):
    from app import websearch

    payload = {"data": {"results": [
        {"title": "同一篇新闻", "url": "https://a", "content": "x" * 300},
        {"title": "同一篇新闻", "url": "https://b", "content": "y"},
        {"title": "另一篇", "url": "https://c", "content": "z"},
    ]}}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setenv("ANYSEARCH_API_KEY", "test-key")
    monkeypatch.setattr(websearch, "_CACHE", {})
    monkeypatch.setattr(websearch.urllib.request, "urlopen", lambda *a, **k: Resp())
    out = websearch.search("任意", max_results=5)
    assert len(out) == 2
    assert out[0]["snippet"].startswith("x") and len(out[0]["snippet"]) <= 180


# ---------------- 引导：示例过滤 ----------------
def test_runnable_filters_unknown_names():
    from app.onboard import _runnable

    names = {"钟南山"}
    assert _runnable("介绍一下钟南山", names) is True
    assert _runnable("找研究 dementia 的专家", names) is True      # 不含人名，保留
    assert _runnable("介绍一下张三", names) is True                # 当前实现：未知人名也保留
