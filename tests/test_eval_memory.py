"""记忆评测的判定逻辑与用例集完整性测试（纯函数，不跑对话）。"""
from __future__ import annotations


def test_case_set_shape():
    from app.eval_memory import load_cases
    cases = load_cases()
    assert len(cases) == 50
    from collections import Counter
    c = Counter(x["ability"] for x in cases)
    assert set(c) == {"extraction", "multi_session", "temporal", "update", "abstain"}
    assert all(v == 10 for v in c.values())
    ids = [x["id"] for x in cases]
    assert len(ids) == len(set(ids))


def test_score_fact_any():
    from app.eval_memory import score_case
    case = {"id": "x", "ability": "extraction", "expect": {"fact_any": ["帕金森"]}}
    r = score_case(case, {"answer": "", "refused": False}, ["帕金森"])
    assert r["passed"] is True
    r2 = score_case(case, {"answer": "", "refused": False}, ["阿尔茨海默病"])
    assert r2["passed"] is False


def test_score_fact_none_blocks_stale():
    from app.eval_memory import score_case
    case = {"id": "u", "ability": "update", "expect": {"fact_none": ["阿尔茨海默病"]}}
    assert score_case(case, {"answer": ""}, ["帕金森"])["passed"] is True
    assert score_case(case, {"answer": ""}, ["阿尔茨海默病"])["passed"] is False


def test_score_note_required_and_forbidden():
    from app.eval_memory import NOTE_MARK, score_case
    need = {"id": "n1", "expect": {"note": True}}
    assert score_case(need, {"answer": NOTE_MARK + "方向=X"}, [])["passed"] is True
    assert score_case(need, {"answer": "普通回答"}, [])["passed"] is False

    forbid = {"id": "n2", "expect": {"note": False}}
    assert score_case(forbid, {"answer": "普通回答"}, [])["passed"] is True
    assert score_case(forbid, {"answer": NOTE_MARK + "x"}, [])["passed"] is False


def test_score_refusal():
    from app.eval_memory import score_case
    case = {"id": "r", "expect": {"refused": True}}
    assert score_case(case, {"refused": True}, [])["passed"] is True
    assert score_case(case, {"refused": False}, [])["passed"] is False


def test_temporal_uses_as_of_values():
    from app.eval_memory import score_case
    case = {"id": "t", "ability": "temporal", "expect": {"fact_any": ["旧"]}}
    out = score_case(case, {"answer": ""}, active_values=["新"], as_of_values=["旧"])
    assert out["passed"] is True


def test_empty_expect_passes():
    from app.eval_memory import score_case
    assert score_case({"id": "e"}, {"answer": ""}, [])["passed"] is True
