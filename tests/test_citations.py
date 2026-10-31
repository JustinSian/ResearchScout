"""引用链的入口解析（纯函数）：把"这篇论文引用了谁：X"里的功能词剥掉。"""
from __future__ import annotations


def test_strips_chinese_question_words():
    from app.agent import _cite_query
    assert _cite_query("这篇论文引用了谁：PROTEIN MEASUREMENT") == "PROTEIN MEASUREMENT"
    assert _cite_query("谁引用了 BERT") == "BERT"


def test_strips_english_triggers():
    from app.agent import _cite_query
    assert _cite_query("citations of Attention is all you need") == "Attention is all you need"
    assert _cite_query("who cites BERT") == "BERT"


def test_keeps_title_untouched():
    from app.agent import _cite_query
    assert _cite_query("Attention is all you need") == "Attention is all you need"


def test_empty_is_safe():
    from app.agent import _cite_query
    assert _cite_query("") == ""
    assert _cite_query("引用了谁") == ""


def test_citation_intent_beats_works_intent():
    """'这篇论文引用了谁' 不能被 _CN_WORKS 的'论文'抢走。"""
    from app.agent import _CN_CITE, _CN_WORKS
    q = "这篇论文引用了谁"
    assert _CN_CITE.search(q) and _CN_WORKS.search(q)
