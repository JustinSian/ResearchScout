"""LLM 规划器：把一句话解析成和规则规划器**同结构**的 plan。

设计原则（很重要）：
1. **输出必须可校验**：LLM 只允许输出 `{"tool": ..., "args": {...}}`，
   工具名和参数名都在白名单里，多余字段直接丢掉，不合法就返回 None；
2. **失败必须可退回**：任何异常（没 key、超时、JSON 坏、工具不存在）都返回 None，
   调用方用规则规划器兜底——LLM 是增强，不是单点故障；
3. **成本可计量**：每次返回都带 token 与耗时，供 A/B 对照使用。
"""
from __future__ import annotations

import json
import os

from app import llm

TOOLS = {
    "find_expert": "按条件/主题找专家。参数：country(CN/US/GB..), inst_kind(hospital/university/company/institute), "
                   "inst_name, cites_min(int), sort(relevance|cites|works|impact), limit(int), "
                   "topic(英文主题词), query(原始查询)",
    "profile": "某位专家的画像。参数：name",
    "collaborators": "某位专家的合作者。参数：name, limit",
    "works": "找论文。参数：query, limit",
    "research_area": "某位专家的研究方向。参数：name",
    "compare": "对比两位专家。参数：a, b",
    "citations": "论文引用链（引用了谁 / 谁引用了它 / 两跳）。参数：query, hops(1|2)",
    "task": "多步尽调任务（评审匹配、深度对比、生成报告）。参数：intent, topic, candidate, manuscript",
}

ARG_WHITELIST = {
    "find_expert": {"country", "inst_kind", "inst_name", "cites_min", "sort",
                    "limit", "topic", "query"},
    "profile": {"name"},
    "collaborators": {"name", "limit"},
    "works": {"query", "limit"},
    "research_area": {"name"},
    "compare": {"a", "b"},
    "citations": {"query", "hops"},
    "task": {"intent", "topic", "candidate", "manuscript"},
}

SYSTEM = (
    "你是学术专家库的意图路由器。用户会给一句中文或英文问题，你输出**一个 JSON 对象**，"
    "形如 {\"tool\": \"find_expert\", \"args\": {...}}。只输出 JSON，不要解释。\n"
    "可用工具与参数：\n" + "\n".join(f"- {k}: {v}" for k, v in TOOLS.items()) + "\n"
    "规则：\n"
    "1. 用户问某人的信息 → profile；问研究方向 → research_area；问合作者 → collaborators；\n"
    "2. 问'谁研究X''找X领域的专家' → find_expert，topic 用英文；\n"
    "3. 问'引用了谁''谁引用了它''两跳' → citations；\n"
    "4. 要求评审匹配/深度对比/生成报告 → task；\n"
    "5. 问论文 → works；\n"
    "6. 有国家/机构/被引下限等硬条件时，填到 find_expert 的对应参数里；\n"
    "7. 无法判断时用 find_expert，并把原句放进 query。\n"
)


def build_prompt(query: str, context_hint: str = "") -> str:
    parts = [f"用户问题：{query}"]
    if context_hint:
        parts.append(f"上一轮结果提示（若本句有指代，按它理解）：{context_hint}")
    parts.append("请输出 JSON。")
    return "\n".join(parts)


def parse_plan(text: str) -> dict | None:
    """把模型输出解析并校验成 plan；不合法返回 None（调用方退回规则）。"""
    if not text:
        return None
    try:
        obj = json.loads(text) if isinstance(text, str) else text
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    tool = obj.get("tool")
    if tool not in TOOLS:
        return None
    raw_args = obj.get("args") or {}
    if not isinstance(raw_args, dict):
        raw_args = {}
    allowed = ARG_WHITELIST.get(tool, set())
    args = {k: v for k, v in raw_args.items() if k in allowed and v not in (None, "")}
    # 类型收敛：cites_min/limit 必须是 int，否则丢掉（避免下游 SQL 出错）
    for key in ("cites_min", "limit"):
        if key in args:
            try:
                args[key] = int(args[key])
            except Exception:
                args.pop(key, None)
    if tool == "citations" and "hops" in args:
        try:
            args["hops"] = 1 if int(args["hops"]) <= 1 else 2
        except Exception:
            args.pop("hops", None)
    return {"tool": tool, "args": args}


def plan(query: str, context_hint: str = "") -> dict | None:
    """返回 {"tool","args","planner":"llm","usage","cost_usd","ms"} 或 None。"""
    if not llm.configured():
        return None
    res = llm.chat_json(SYSTEM, build_prompt(query, context_hint))
    if not res.get("ok"):
        return None
    parsed = parse_plan(res.get("data"))
    if not parsed:
        return None
    return {**parsed, "planner": "llm",
            "usage": res.get("usage") or {}, "cost_usd": res.get("cost_usd", 0.0),
            "ms": res.get("ms", 0)}


def enabled() -> bool:
    """LLM 规划是否启用：ST_PLANNER=llm|auto 且 key 已配置。"""
    mode = (os.environ.get("ST_PLANNER") or "rules").strip().lower()
    return mode in ("llm", "auto") and llm.configured()


_KIND_ZH = {"hospital": "医院", "university": "高校", "company": "企业", "institute": "研究所"}
_SORT_ZH = {"cites": "被引最多", "works": "论文最多", "recent": "最近活跃"}


def _compose_find_query(args: dict, original: str) -> str:
    """把 LLM 给的结构化条件拼回一句人话。

    为什么不用 `**args` 直接调工具：`tool_find_expert(query, limit, sid)` 没有
    country/topic 这些形参，多传一个键就 TypeError。而现有的 facet 解析器本来就
    能从"中国的医院 呼吸 专家 被引最多"里抽出全部条件——所以拼成句子最稳。
    """
    if args.get("query"):
        return str(args["query"])
    from app.facets import COUNTRY_ZH
    bits = []
    cc = str(args.get("country") or "").upper()
    if cc:
        bits.append(COUNTRY_ZH.get(cc, cc))
    bits.append(_KIND_ZH.get(str(args.get("inst_kind") or "").lower(), ""))
    bits.append(str(args.get("inst_name") or ""))
    if args.get("cites_min"):
        bits.append(f"被引超过{args['cites_min']}")
    if args.get("sort"):
        bits.append(_SORT_ZH.get(str(args["sort"]).lower(), ""))
    bits.append(str(args.get("topic") or ""))
    bits.append("专家")
    q = " ".join(b for b in bits if b).strip()
    return q or original


def to_call(parsed: dict, original: str) -> dict | None:
    """把校验过的 plan 映射成**真实工具签名**可接受的调用。"""
    if not parsed:
        return None
    tool, args = parsed.get("tool"), dict(parsed.get("args") or {})
    limit = args.get("limit")
    try:
        limit = max(1, min(int(limit), 50)) if limit else None
    except Exception:
        limit = None

    if tool == "find_expert":
        call_args = {"query": _compose_find_query(args, original)}
        if limit:
            call_args["limit"] = limit
        return {"tool": "find_expert", "args": call_args}
    if tool == "profile":
        return {"tool": "profile", "args": {"name": args.get("name") or original}}
    if tool == "research_area":
        return {"tool": "research_area", "args": {"name": args.get("name") or original}}
    if tool == "collaborators":
        call_args = {"name": args.get("name") or original}
        if limit:
            call_args["limit"] = limit
        return {"tool": "collaborators", "args": call_args}
    if tool == "works":
        call_args = {"query": args.get("query") or original}
        if limit:
            call_args["limit"] = limit
        return {"tool": "works", "args": call_args}
    if tool == "citations":
        try:
            hops = 1 if int(args.get("hops") or 1) <= 1 else 2
        except Exception:
            hops = 1
        return {"tool": "citations", "args": {"query": args.get("query") or original,
                                             "hops": hops}}
    if tool == "compare":
        a, b = args.get("a"), args.get("b")
        if not (a and b):
            return None
        return {"tool": "compare", "args": {"names": [str(a), str(b)]}}
    if tool == "task":
        intent = str(args.get("intent") or "").strip()
        bits = [str(args.get("topic") or ""), str(args.get("candidate") or ""),
                str(args.get("manuscript") or ""), intent]
        q = " ".join(b for b in bits if b).strip() or original
        return {"tool": "task", "args": {"query": q}}
    return None
