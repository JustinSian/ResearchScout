"""核心单元测试：不依赖模型、不依赖网络、不依赖真实数据库。

设计原则：
- 能纯函数测的，绝不碰 DB / 网络（hooks、narrate、memory.task_key、skills 参数解析）；
- 需要 DB 的，用临时库并在 fixture 里建最小 schema；
- 需要重依赖（langgraph / transformers）的，用 importorskip 跳过，
  保证 CI 里只装 pytest + fastapi 也能跑起来。
"""
from __future__ import annotations

import os
import tempfile

import pytest


# ---------------- 中间件：脱敏 ----------------
def test_redact_masks_email_phone_and_long_id():
    from app.hooks import redact

    s = redact("联系 zhangsan@example.com 或 13800138000，OpenAlex A5101475024")
    assert "[email]" in s
    assert "[phone]" in s
    # 长 ID 必须先于手机号匹配，否则会被吃成 A[phone]
    assert "A[phone]" not in s
    assert "[id]" in s


def test_redact_keeps_plain_names():
    from app.hooks import redact

    assert redact("钟南山") == "钟南山"
    assert redact("Nanshan Zhong") == "Nanshan Zhong"


# ---------------- 中间件：限流 ----------------
def test_token_bucket_exhausts():
    """take() 用返回值表示"够不够"，抛异常的是 before_tool 那一层。"""
    from app.hooks import TokenBucket

    b = TokenBucket(capacity=3, refill_per_min=0)
    assert [b.take() for _ in range(3)] == [True, True, True]
    assert b.take() is False


def test_before_tool_weights_expensive_tools():
    """高成本工具消耗 2 个令牌，应有更早被限流的行为（这里只验证签名与不抛错）。"""
    from app import hooks

    hooks._BUCKET.tokens = 100
    hooks.before_tool("researcher", {})     # 权重 2
    hooks.before_tool("report", {})         # 权重 1


# ---------------- 叙述层 ----------------
def test_narrate_speaks_like_an_agent():
    from app import narrate

    opener = narrate.opening("比较钟南山和李兰娟", "compare_experts")
    assert "对比" in opener or "比较" in opener
    assert narrate.closing(True, "无证据").startswith("资料不足")
    assert narrate.caveat("reviewer_match").startswith("提醒")


# ---------------- 记忆：缓存键 ----------------
def test_task_key_is_stable_and_discriminating():
    from app.memory import task_key

    a = task_key("kb", "钟南山的研究方向")
    b = task_key("kb", "钟南山的研究方向。")      # 标点不同应视为同一问题
    c = task_key("kb", "李兰娟的研究方向")
    assert a == b
    assert a != c


# ---------------- Skills ----------------
def test_skill_weights_are_loaded_from_markdown():
    from app.skills import list_skills, load_skill, param

    names = [s["name"] for s in list_skills()]
    assert "reviewer-matching" in names
    w = param("reviewer-matching", "weights")
    assert set(w) == {"topic", "h", "recent"}
    assert abs(sum(w.values()) - 1.0) < 1e-6
    body = load_skill("reviewer-matching")["body"]
    assert "审稿人匹配" in body


# ---------------- 兜底检索：无 key 时不发请求 ----------------
def test_websearch_without_key_returns_empty(monkeypatch):
    from app import websearch

    monkeypatch.delenv("ANYSEARCH_API_KEY", raising=False)
    assert websearch.available() is False
    assert websearch.search("任意问题") == []


def test_websearch_answer_formats_sources(monkeypatch):
    from app import websearch

    fake = [{"title": "某新闻", "url": "https://example.com/a", "snippet": "摘要内容"}]
    monkeypatch.setattr(websearch, "search", lambda q, max_results=5: fake)
    out = websearch.answer("任意问题")
    assert out["ok"] is True
    assert "未经本库的证据门槛核验" in out["text"]
    assert out["citations"][0]["url"] == "https://example.com/a"


# ---------------- 引导 ----------------
def test_welcome_has_groups_and_limits(tmp_path, monkeypatch):
    """welcome() 会查库；这里只验证结构契约，用最小临时库。"""
    from app import config

    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    from app import db as dbmod

    monkeypatch.setattr(dbmod, "DB_PATH", db)
    dbmod.init_db()

    # welcome() 会查 dossier 表，但它不在 schema.sql 里（建表分散在各模块），
    # 测试里显式补一下。顺带记一笔：建表语句应该集中管理。
    from app.build_dossiers import DDL as DOSS_DDL
    from app.db import connect as _connect

    conn = _connect()
    try:
        for ddl in DOSS_DDL:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()

    from app import onboard

    w = onboard.welcome()
    assert w["greeting"]
    assert len(w["groups"]) >= 1
    assert len(w["limits"]) >= 4
    assert {"people", "works", "dossiers"} <= set(w["stats"])


# ---------------- 调度：路由规则 ----------------
pytestmark = pytest.mark.filterwarnings("ignore")


def _decide(q):
    pytest.importorskip("langgraph")
    from app.supervisor import decide

    return decide(q)


def test_route_review_to_task():
    assert _decide("谁更适合审一篇关于肺癌免疫治疗的稿件")["route"] == "task"


def test_route_out_of_kb_to_web():
    assert _decide("今天北京天气怎么样")["route"] == "web"
    assert _decide("2026年诺贝尔生理学或医学奖得主是谁")["route"] == "web"


def test_route_person_plus_recency_to_hybrid():
    assert _decide("钟南山最近有什么新闻")["route"] == "hybrid"


def test_route_recency_without_person_to_web():
    assert _decide("最近 AI 领域有什么突破")["route"] == "web"


# ---------------- 仲裁合并：分节标注 ----------------
def test_merge_keeps_kb_and_web_in_separate_sections():
    from app.supervisor import _merge

    kb = {"refused": False, "answer": "库内结论", "citations": [{"kind": "person", "id": 1}]}
    web = {"ok": True, "text": "网络结论", "citations": [{"kind": "web", "url": "https://x"}]}
    out = _merge("q", kb, web)
    assert "库内结论" in out["answer"] and "网络结论" in out["answer"]
    assert "未经本库核验" in out["answer"]
    assert len(out["citations"]) == 2


def test_merge_refuses_when_both_empty():
    from app.supervisor import _merge

    out = _merge("q", {"refused": True, "answer": "", "reason": "库内没有"}, None)
    assert out["refused"] is True
    assert "资料不足" in out["answer"]


# ---------------- 可信判定（纯函数） ----------------
def test_judge_rejects_non_person_entity():
    from app.judge import judge

    res = judge({"name": "某某大学研究团队", "works_count": 3})
    assert res["verdict"] != "accept"


def test_judge_accepts_well_evidenced_person():
    from app.judge import judge

    res = judge({"name": "Nanshan Zhong", "works_count": 900,
                 "cited_by_count": 60000, "has_institution": True,
                 "has_topics": True, "orcid": "0000-0002-1825-0097",
                 "cite_p50": 5504, "cite_p90": 18368, "external_verified": True})
    assert res["verdict"] == "accept"
    assert res["score"] >= 0.8
