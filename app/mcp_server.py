"""MCP server：把这套能力暴露给 Cursor / Claude 等 MCP 客户端。

为什么做：能力再好，如果只能通过 HTTP 手调，就只是"一个 demo 服务"。
MCP 是目前 agent 客户端的事实标准，暴露成 MCP 工具之后，任意支持 MCP 的
编辑器/助手都能直接调用——这是从"能跑"到"能被用"的一步。

实现取舍：**不引入 mcp SDK**，直接实现 JSON-RPC over stdio 的最小子集
（initialize / tools/list / tools/call / ping）。理由：
- 只需 4 个方法，SDK 反而增加依赖与版本约束；
- 协议是稳定的（2024-11-05 版），自己写 100 行可控、可测、可解释。

工具清单（都是只读或幂等的）：
  search_experts   按主题/关键词找专家
  get_profile      专家画像（含深档案指标）
  compare_experts  两位专家方向对比（多步任务，产出报告）
  find_reviewers   审稿人匹配（researcher/analyst/verifier 协作）
  ask              统一问答入口（Supervisor 路由 kb/task/web/hybrid）
  health           数据健康度与规模

启动：
  python -m app.mcp_server            # stdio
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

PROTOCOL = "2024-11-05"
SERVER = {"name": "scholartrust", "version": "0.1.0"}

TOOLS: list[dict] = [
    {
        "name": "search_experts",
        "description": "按研究主题或关键词检索库内专家，返回姓名/机构/被引等基本信息。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "主题或关键词，如 dementia / lung cancer"},
                "limit": {"type": "integer", "description": "返回数量，默认 5", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_profile",
        "description": "获取某位专家的画像；若有深档案，额外返回 h-index、近五年方向与代表作。",
        "inputSchema": {
            "type": "object",
            "properties": {"pid": {"type": "integer", "description": "专家 pid"}},
            "required": ["pid"],
        },
    },
    {
        "name": "compare_experts",
        "description": "对比两位专家近五年的研究方向差异，产出带证据的对照报告（多步任务）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "string", "description": "第一位专家（中文名或英文名）"},
                "b": {"type": "string", "description": "第二位专家"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "find_reviewers",
        "description": "给定稿件主题，召回审稿人候选并做领域校验（会剔除同名不同人）。",
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "description": "稿件主题，如 肺癌免疫治疗"}},
            "required": ["topic"],
        },
    },
    {
        "name": "ask",
        "description": "统一问答入口：库内能答则答，库外问题转公开网络检索（会标注未核验）。",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "health",
        "description": "返回数据规模与六项健康检查结果。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ---------------- 工具实现 ----------------
def _search_experts(query: str, limit: int = 5) -> dict:
    from app.experts import top_experts
    data = top_experts(sort="cites", topic=query, limit=min(int(limit), 20))
    return {"count": data.get("total", 0),
            "items": [{"pid": i["pid"], "name": i.get("name_zh") or i["name"],
                       "inst": i.get("last_inst"), "works": i.get("works_count"),
                       "cites": i.get("cited_by_count")} for i in data.get("items", [])]}


def _get_profile(pid: int) -> dict:
    from app.dossiers import dossier
    from app.search import person_profile
    prof = person_profile(int(pid))
    if not prof:
        return {"error": f"没有 pid={pid} 的专家"}
    d = dossier(int(pid))
    out = {"pid": prof["pid"], "name": prof.get("name_zh") or prof.get("name"),
           "inst": prof.get("last_inst"), "works": prof.get("works_count"),
           "cites": prof.get("cited_by_count"),
           "topics": prof.get("topics_zh") or prof.get("topics")}
    if d:
        m = d["metrics"]
        out["dossier"] = {"works": m.get("works"), "h_index": m.get("h_index"),
                          "first_year": m.get("first_year"), "last_year": m.get("last_year"),
                          "recent_rep": (m.get("rep_papers") or [])[:3]}
    else:
        out["dossier"] = None
    return out


def _compare_experts(a: str, b: str) -> dict:
    from app.harness import run, vfs_read
    out = run(f"比较{a}和{b}近五年的研究方向差异")
    return {"tid": out["tid"], "route": "task",
            "report": (vfs_read(out.get("report_path") or "") or "")[:4000]}


def _find_reviewers(topic: str) -> dict:
    from app.harness import run, vfs_read
    out = run(f"谁更适合审一篇关于{topic}的稿件")
    return {"tid": out["tid"], "report": (vfs_read(out.get("report_path") or "") or "")[:4000]}


def _ask(query: str) -> dict:
    from app.supervisor import run
    r = run(query, log=lambda *_: None)
    return {"route": r["route"], "refused": r["refused"],
            "answer": r["answer"][:4000],
            "citations": r.get("citations", [])[:5]}


def _health() -> dict:
    from app.health import trust_health
    from app.db import connect
    h = trust_health()
    c = connect()
    try:
        stats = {"people": c.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                 "works": c.execute("SELECT COUNT(*) FROM works").fetchone()[0],
                 "dossiers": c.execute("SELECT COUNT(*) FROM dossier").fetchone()[0]}
    finally:
        c.close()
    return {"ok": h["ok"], "stats": stats,
            "checks": [{"name": x["name"], "status": x["status"], "value": x["value"]}
                       for x in h["checks"]]}


HANDLERS: dict[str, Callable[..., dict]] = {
    "search_experts": _search_experts,
    "get_profile": _get_profile,
    "compare_experts": _compare_experts,
    "find_reviewers": _find_reviewers,
    "ask": _ask,
    "health": _health,
}


# ---------------- JSON-RPC ----------------
def _ok(mid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    """处理一条 JSON-RPC 消息；通知类消息返回 None。"""
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        return _ok(mid, {"protocolVersion": PROTOCOL,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": SERVER})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name or "")
        if not fn:
            return _err(mid, -32601, f"unknown tool: {name}")
        try:
            data = fn(**args)
        except TypeError as exc:
            return _err(mid, -32602, f"bad arguments for {name}: {exc}")
        except Exception as exc:      # 工具失败要让客户端看到原因，而不是断连
            return _ok(mid, {"content": [{"type": "text",
                                          "text": f"工具执行失败：{type(exc).__name__}: {exc}"}],
                             "isError": True})
        return _ok(mid, {"content": [{"type": "text",
                                      "text": json.dumps(data, ensure_ascii=False)}],
                         "isError": False})
    return _err(mid, -32601, f"unknown method: {method}")


def main() -> None:
    """stdio 主循环：一行一条 JSON-RPC 消息（MCP stdio 传输）。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_err(None, -32700, "parse error")), flush=True)
            continue
        resp = handle(msg)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
