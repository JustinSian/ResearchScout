"""读鉴权中间件的测试：ST_READ_AUTH=1 时读接口要求 X-API-Key。"""
from __future__ import annotations

import asyncio

import pytest

from app.api import read_auth_middleware


class _Req:
    def __init__(self, path, key=None):
        self.url = type("U", (), {"path": path})()
        self.headers = {"x-api-key": key} if key else {}


async def _ok(request):
    return "OK"


def _run(path, key, env):
    import os
    import app.api as api
    monkey = pytest.MonkeyPatch()
    monkey.setenv("ST_API_KEY", env.get("key", "secret"))
    monkey.setenv("ST_READ_AUTH", env.get("auth", "1"))
    try:
        return asyncio.run(read_auth_middleware(_Req(path, key), _ok))
    finally:
        monkey.undo()


def test_open_paths_never_gated():
    for p in ("/", "/status", "/model/status", "/health", "/ui/x.js"):
        assert _run(p, None, {}) == "OK"


def test_read_gated_without_key():
    r = _run("/stats", None, {})
    assert r.status_code == 401


def test_read_allowed_with_correct_key():
    assert _run("/stats", "secret", {}) == "OK"


def test_read_open_when_auth_off():
    assert _run("/stats", None, {"auth": "0"}) == "OK"
