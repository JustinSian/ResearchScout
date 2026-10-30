"""MCP server 协议层测试：不依赖数据库与网络。

只测协议契约（握手、工具清单、未知方法/工具、通知不回包），
具体工具实现由各自的模块测试覆盖。
"""
from __future__ import annotations


def test_initialize_returns_protocol_and_server_info():
    from app.mcp_server import handle

    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r["jsonrpc"] == "2.0"
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"]["name"] == "scholartrust"
    assert "tools" in r["result"]["capabilities"]


def test_tools_list_has_expected_tools_and_schemas():
    from app.mcp_server import handle

    r = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in r["result"]["tools"]]
    assert set(names) == {"search_experts", "get_profile", "compare_experts",
                          "find_reviewers", "ask", "health"}
    by_name = {t["name"]: t for t in r["result"]["tools"]}
    # 每个工具都要有可校验的 inputSchema 与描述，否则客户端无法正确调用
    for t in r["result"]["tools"]:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    assert by_name["get_profile"]["inputSchema"]["required"] == ["pid"]


def test_notification_returns_nothing():
    """通知（无 id）不需要回包，否则客户端会报协议错误。"""
    from app.mcp_server import handle

    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_and_tool_return_jsonrpc_errors():
    from app.mcp_server import handle

    r1 = handle({"jsonrpc": "2.0", "id": 3, "method": "no/such"})
    assert r1["error"]["code"] == -32601
    r2 = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "nope", "arguments": {}}})
    assert r2["error"]["code"] == -32601
    assert "unknown tool" in r2["error"]["message"]


def test_ping():
    from app.mcp_server import handle

    assert handle({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}
