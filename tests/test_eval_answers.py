"""答案级评测的判定逻辑测试（纯函数，不跑 agent）。

gold set 在 eval/answers.jsonl；这里只测"约束检查"本身：
工具、拒答、引用、必须有/必须没有的片段。
"""
from __future__ import annotations


def test_all_constraints_pass():
    from app.eval_answers import check_answer
    case = {"id": "x", "tools": ["find_expert"], "must_any": ["呼吸", "respiratory"],
            "must_all": ["专家"], "must_not": ["没有找到"], "refuse": False}
    r = {"answer": "呼吸专家：A、B", "plan": [{"tool": "find_expert"}],
         "citations": [{"kind": "person"}], "refused": False}
    out = check_answer(case, r)
    assert out["passed"] is True
    assert all(out["checks"].values())


def test_wrong_tool_fails():
    from app.eval_answers import check_answer
    case = {"tools": ["find_expert"]}
    r = {"answer": "x", "plan": [{"tool": "works"}], "citations": [], "refused": False}
    assert check_answer(case, r)["checks"]["tools_ok"] is False


def test_must_all_requires_every_fragment():
    from app.eval_answers import check_answer
    case = {"must_all": ["钟南山", "李兰娟"]}
    r = {"answer": "只提到钟南山", "plan": [], "citations": [1], "refused": False}
    assert check_answer(case, r)["checks"]["must_all_ok"] is False


def test_must_not_blocks_phrase():
    from app.eval_answers import check_answer
    case = {"must_not": ["没有找到"]}
    r = {"answer": "知识库里没有找到", "plan": [], "citations": [], "refused": True}
    assert check_answer(case, r)["checks"]["must_not_ok"] is False


def test_refusal_needs_no_citation():
    from app.eval_answers import check_answer
    case = {"refuse": True}
    r = {"answer": "资料不足，我不猜。", "plan": [], "citations": [], "refused": True}
    out = check_answer(case, r)
    assert out["checks"]["cited_ok"] is True      # 拒答不需要引用
    assert out["checks"]["refuse_ok"] is True


def test_kb_refuse_allows_web_fallback():
    """库内拒答 + 联网兜底 = 允许（fallback 是设计出来的降级，不是失败）。"""
    from app.eval_answers import check_answer
    case = {"kb_refuse": True}
    r = {"answer": "网络检索结果…", "plan": [], "citations": [1],
         "refused": False, "kb_refused": True}
    assert check_answer(case, r)["checks"]["kb_refuse_ok"] is True


def test_missing_answer_is_safe():
    from app.eval_answers import check_answer
    out = check_answer({"must_any": ["x"]}, {})
    assert out["passed"] is False
    assert out["checks"]["must_any_ok"] is False
