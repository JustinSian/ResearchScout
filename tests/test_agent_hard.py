"""难例相关的纯函数测试：澄清识别、记忆问题识别。"""
from __future__ import annotations
from app.agent_llm import _GENERIC_REQ, _SELF_Q


def test_generic_request_matches_only_bare_forms():
    for q in ("推荐几个专家", "找几个专家", "帮我推荐专家", "来几位学者"):
        assert _GENERIC_REQ.match(q), q
    for q in ("推荐几个呼吸科专家", "找研究帕金森的人", "钟南山的合作者"):
        assert not _GENERIC_REQ.match(q), q


def test_self_question_detects_memory_queries():
    for q in ("我现在研究什么方向？", "我之前说过我的领域吗", "我的研究方向是什么"):
        assert _SELF_Q.search(q), q
    for q in ("谁研究帕金森", "推荐几个专家", "钟南山的论文"):
        assert not _SELF_Q.search(q), q


def test_country_constraint_extraction():
    from app.memory_extract import extract
    got = extract("我只找中国的专家")
    assert {"op": "set", "key": "country", "value": "中国"} in got


def test_question_words_block_disclosure():
    from app.agent_llm import _is_disclosure
    # "我的研究领域是什么" 是提问，不是自述（曾因缺"什么"被误判为自述）
    assert _is_disclosure("我的研究领域是什么") is False
    assert _is_disclosure("我研究帕金森") is True
