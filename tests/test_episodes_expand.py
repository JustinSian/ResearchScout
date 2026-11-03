"""经验记忆（episodes）与库扩充（expand）的测试。

episodes 用临时库；expand 全部用假 OpenAlex，不触网。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from app import user_memory
    monkeypatch.setattr(user_memory, "MEM_DB", tmp_path / "mem.db")
    user_memory.init()
    return user_memory


# ---------------- episodes ----------------

def test_record_requires_success(mem):
    from app import episodes
    assert episodes.record("找专家", {"refused": True, "evidence": []}) is None
    assert episodes.record("找专家", {"refused": False, "evidence": [{"items": []}]}) is None


def test_record_and_recall(mem):
    from app import episodes
    result = {"refused": False, "cost_usd": 0.01, "ms": 900,
              "steps": [{"tool": "find_expert"}, {"tool": "profile"}],
              "evidence": [{"items": [{"pid": 527}, {"pid": 503}]}]}
    eid = episodes.record("找研究帕金森的专家", result, uid="u1", require_match=False)
    assert eid
    hits = episodes.recall("推荐帕金森专家")
    assert hits and hits[0]["id"] == eid
    assert "find_expert" in hits[0]["tools_json"]


def test_recall_returns_empty_when_unrelated(mem):
    from app import episodes
    episodes.record("找研究图神经网络的专家",
                    {"refused": False, "evidence": [{"items": [{"pid": 1}]}]},
                    require_match=False)
    assert episodes.recall(" medieval icelandic poetry ") == []


def test_hint_contains_tools_and_pids(mem):
    from app import episodes
    episodes.record("找研究肺癌免疫的专家",
                    {"refused": False, "steps": [{"tool": "find_expert"}],
                     "evidence": [{"items": [{"pid": 42}]}]}, require_match=False)
    text = episodes.hint("肺癌免疫专家")
    assert "同类检索经验" in text and "42" in text and "find_expert" in text


def test_stats(mem):
    from app import episodes
    episodes.record("找研究帕金森的专家",
                    {"refused": False, "evidence": [{"items": [{"pid": 1}, {"pid": 2}]}]},
                    require_match=False)
    s = episodes.stats()
    assert s["episodes"] == 1 and s["avg_pids"] == 2.0


# ---------------- expand ----------------

def test_expand_rejects_generic_topic(monkeypatch, mem):
    from app import expand
    out = expand.expand_topic("medicine")
    assert out["ok"] is False and "泛" in out["reason"]


def test_expand_rejects_short_topic(mem):
    from app import expand
    assert expand.expand_topic("a")["ok"] is False


def test_expand_skips_recent(monkeypatch, mem):
    from app import expand
    monkeypatch.setattr(expand, "recently_expanded", lambda *a, **k: True)
    out = expand.expand_topic("niemann-pick disease")
    assert out["ok"] is False and "24" in out["reason"]


def test_expand_inserts_person_and_work(tmp_path, monkeypatch, mem):
    from app import expand
    from app.db import connect, init_db
    monkeypatch.setattr("app.config.DB_PATH", tmp_path / "scholar.db")
    import app.db as dbmod
    monkeypatch.setattr(dbmod, "DB_PATH", tmp_path / "scholar.db")
    monkeypatch.setattr(expand, "connect", lambda: dbmod.connect())
    init_db()

    class FakeOA:
        def _get(self, path, params):
            if path == "/works":          # 主题检索：先找论文
                return {"results": [{
                    "id": "https://openalex.org/W1", "title": "Niemann-Pick disease study",
                    "cited_by_count": 100,
                    "authorships": [{"author": {"id": "https://openalex.org/A1",
                                                "display_name": "Test Author",
                                                "orcid": None}}],
                }]}
            if path.startswith("/authors/"):   # 再取作者详情
                return {"id": "https://openalex.org/A1", "display_name": "Test Author",
                        "orcid": None, "works_count": 30, "cited_by_count": 500,
                        "last_known_institutions": [{"display_name": "Test Univ",
                                                     "country_code": "CN"}],
                        "topics": [{"display_name": "Niemann-Pick disease"}]}
            return {}

        def works_of_author(self, oid, per_page=10):
            return [{"id": "https://openalex.org/W1", "doi": "10.1/x",
                     "title": "Niemann-Pick study", "publication_year": 2020,
                     "cited_by_count": 20, "type": "article",
                     "primary_location": {"source": {"display_name": "J"}},
                     "authorships": [], "abstract_inverted_index": None}]

    monkeypatch.setattr(expand, "_OA", FakeOA())
    out = expand.expand_topic("Niemann-Pick disease")
    assert out["ok"] is True and out["added_people"] == 1 and out["added_works"] == 1
    conn = connect()
    try:
        row = conn.execute("SELECT name, status, source FROM people").fetchone()
        assert row["name"] == "Test Author"
        assert row["status"] == "published"       # 强作者自动发布
        assert row["source"] == "auto_expand"
        assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
    finally:
        conn.close()

    # 24 小时内不再重复扩充
    assert expand.expand_topic("Niemann-Pick disease")["ok"] is False


def test_history_records_actions(mem):
    from app import expand
    expand._log("x", 2, 3, [1, 2], "pending")
    rows = expand.history()
    assert rows and rows[0]["added_people"] == 2
