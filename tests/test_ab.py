"""A/B 对照的聚合逻辑测试（纯函数，不跑 agent）。

`_agg` 负责把每次运行的行汇总成可比指标；这里只验证口径：
通过率、工具调用总数、联网率、拒答率、延迟分位、失败 ID 列表。
"""
from __future__ import annotations


def _row(passed=True, ms=100.0, tools=1, web=False, refused=False, cid="x"):
    return {"quality": {"id": cid, "passed": passed}, "ms": ms,
            "tool_calls": tools, "web_hit": web, "refused": refused,
            "tools": ["find_expert"], "route": "kb", "citations": 1}


def test_agg_counts_and_rates():
    from app.ab_single_vs_multi import _agg
    rows = [_row(passed=True), _row(passed=False, web=True, cid="y"),
            _row(passed=True, tools=2), _row(passed=True, refused=True)]
    s = _agg(rows)
    assert s["n"] == 4
    assert s["pass_rate"] == 0.75
    assert s["tool_calls_total"] == 5
    assert s["tool_calls_mean"] == 1.25
    assert s["web_hit_rate"] == 0.25
    assert s["refuse_rate"] == 0.25
    assert s["failed_ids"] == ["y"]


def test_agg_latency_percentiles():
    from app.ab_single_vs_multi import _agg
    rows = [_row(ms=v) for v in (10, 20, 30, 40, 100)]
    s = _agg(rows)
    assert s["latency_p50"] == 30
    # p95 用 max(0, int(n*0.95)-1)=3 → 第 4 个（40）
    assert s["latency_p95"] == 40


def test_agg_empty_is_safe():
    from app.ab_single_vs_multi import _agg
    s = _agg([])
    assert s["n"] == 0 and s["pass_rate"] == 0
    assert s["latency_p50"] == 0


def test_agg_all_failed():
    from app.ab_single_vs_multi import _agg
    rows = [_row(passed=False, cid="a"), _row(passed=False, cid="b")]
    s = _agg(rows)
    assert s["pass_rate"] == 0 and s["failed_ids"] == ["a", "b"]
