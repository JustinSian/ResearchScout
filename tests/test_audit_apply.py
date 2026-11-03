"""盲审答案解析的测试。"""
from __future__ import annotations


def test_parse_answers_extracts_label_and_note(tmp_path):
    from app.audit_apply import parse_answers
    p = tmp_path / "a.txt"
    p.write_text("1 张三 | 主题A | 是 | 一作论文\n2 李四 | 主题B | 边界 | 仅合著\n", encoding="utf-8")
    ans = parse_answers(p)
    assert ans[1] == ("是", "一作论文")
    assert ans[2][0] == "边界"


def test_parse_order_maps_numbers_to_opaque_ids(tmp_path):
    from app.audit_apply import parse_order
    p = tmp_path / "o.txt"
    p.write_text(" 1. [R8b33e968] 问题：X\n   名字\n", encoding="utf-8")
    assert parse_order(p) == {1: "R8b33e968"}


def test_wilson_bounds():
    from app.audit_apply import wilson
    lo, hi = wilson(12, 14)
    assert 0 <= lo < 12 / 14 < hi <= 1
