"""真实流式输出（chat_stream）的测试：不依赖网络，用假的 httpx 客户端。"""
from __future__ import annotations

import json

import pytest

from app import llm

LINES = [
    'data: {"choices":[{"delta":{"content":"你好"}}]}',
    'data: {"choices":[{"delta":{"content":"，世界"}}]}',
    'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":7}}',
    "data: [DONE]",
]


class _FakeStream:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return b""


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return _FakeStream(self._lines)

    def post(self, *a, **kw):
        raise RuntimeError("不该走到非流式回退")


@pytest.fixture()
def fake(monkeypatch):
    def _install(lines):
        monkeypatch.setattr(llm, "configured", lambda: True)
        monkeypatch.setattr(llm, "api_key", lambda: "k")
        monkeypatch.setattr(llm, "base_url", lambda: "http://fake")
        monkeypatch.setattr(llm, "model", lambda: "m")
        monkeypatch.setattr(llm.httpx, "Client", lambda *a, **k: _FakeClient(lines))
    return _install


def test_chat_stream_deltas_and_text(fake):
    fake(LINES)
    got = []
    r = llm.chat_stream([{"role": "user", "content": "hi"}], on_delta=got.append)
    assert r["ok"] is True
    assert r["text"] == "你好，世界"
    assert got == ["你好", "，世界"]
    assert r["usage"]["prompt_tokens"] == 11
    assert r["usage"]["completion_tokens"] == 7


def test_chat_stream_ignores_broken_callback(fake):
    fake(LINES)

    def boom(_piece):
        raise RuntimeError("前端断开了")

    r = llm.chat_stream([{"role": "user", "content": "hi"}], on_delta=boom)
    assert r["ok"] is True and r["text"] == "你好，世界"


def test_chat_stream_falls_back_when_http_fails(fake, monkeypatch):
    fake(['data: {"error": "boom"}'])
    monkeypatch.setattr(llm, "chat_text",
                        lambda messages, timeout=60.0: {"ok": True, "text": "兜底答案",
                                                        "usage": {}, "cost_usd": 0.0, "ms": 1})
    # 让流式请求直接失败
    class _Boom(_FakeClient):
        def stream(self, method, url, **kw):
            raise RuntimeError("network down")
    monkeypatch.setattr(llm.httpx, "Client", lambda *a, **k: _Boom(LINES))
    r = llm.chat_stream([{"role": "user", "content": "hi"}])
    assert r["ok"] is True and r["text"] == "兜底答案"
    assert r.get("stream_fallback") is True
