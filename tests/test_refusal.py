"""拒答识别的回归测试：模型换说法也不能判错。"""
from __future__ import annotations
import pytest
from app.agent_llm import _looks_refusal


@pytest.mark.parametrize("text", [
    "库里没有研究 quantum gravity phenology 的专家。",
    "没有找到研究该方向的专家。",
    "库里没找到相关的专家。",
    "查不到相关专家，建议换关键词。",
    "数据库里未检索到匹配的学者。",
    "没有可信的专家可以推荐。",
])
def test_refusal_variants_are_detected(text):
    assert _looks_refusal(text) is True


@pytest.mark.parametrize("text", [
    "钟南山是呼吸病学专家，被引 68,983 次。",
    "推荐三位做 Gaucher disease 的专家：Peter Meikle 等。",
    "该团队 2023 年发表在 Nature 的论文讨论了此事。",
])
def test_normal_answers_are_not_refusal(text):
    assert _looks_refusal(text) is False
