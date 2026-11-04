"""聊天限流的测试：每-IP 令牌桶，超限拒绝。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.fixture()
def rate(monkeypatch):
    import app.api as api
    api._RATE.clear()
    monkeypatch.setenv("ST_CHAT_BURST", "3")
    monkeypatch.setenv("ST_CHAT_RPM", "60")   # 大 RPM，避免时间流逝影响
    return api


def test_burst_then_deny(rate):
    for _ in range(3):
        assert rate._chat_allow("1.2.3.4") is True
    assert rate._chat_allow("1.2.3.4") is False


def test_different_ips_are_independent(rate):
    for _ in range(3):
        assert rate._chat_allow("1.1.1.1") is True
    assert rate._chat_allow("2.2.2.2") is True


def test_gate_raises_429_on_deny(rate):
    for _ in range(3):
        rate._chat_allow("9.9.9.9")
    with pytest.raises(HTTPException) as ei:
        rate._chat_gate(_FakeRequest("9.9.9.9"))
    assert ei.value.status_code == 429


def test_gate_tolerates_none_request(rate):
    # 直接调用（无 Request 注入）不应崩溃
    rate._chat_gate(None)


class _FakeRequest:
    def __init__(self, host):
        self.client = _FakeClient(host)


class _FakeClient:
    def __init__(self, host):
        self.host = host
