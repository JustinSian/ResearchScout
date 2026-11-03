"""ResearchScout 的 Agent 主循环：LLM 在环 + 长短期记忆 + 可追溯 trace。

和旧 deterministic 路径的区别：
  旧：规则路由 → 单次工具调用 → 模板拼答案（无 LLM）
  新：模型读「长期记忆 + 本会话摘要 + 最近几轮」→ 自己决定调哪个工具、调几次
      → 基于工具结果写答案 → 写回记忆

组成：
  - 工具：7 个知识库工具 + 1 个 `remember`（模型可主动记事实）
  - 短期记忆：session_memory（滚动摘要 + 最近 8 轮）
  - 长期记忆：user_memory / memory_recall（只读事实，写入走 memory_extract）
  - RSI：active_policy().plan_prompt 追加到 system prompt（受评测闸门约束）
  - 预算：max_calls / budget_usd / deadline，超限强制收尾作答
"""
from __future__ import annotations

import json
import os
import time

from app import llm, memory_extract, memory_recall, rsi, session_memory, user_memory, episodes

MAX_STEPS = 3          # 轮数
MAX_CALLS = 4          # 工具调用总数（并行调用也算）
BUDGET_USD = 0.03

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "find_expert",
        "description": "按主题/条件在专家库里找人（支持国家、机构类型、被引下限、排序）。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索语句，可含国家/机构/被引条件"},
            "limit": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "profile",
        "description": "某位专家的画像：作品数、被引、机构、代表作。",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "research_area",
        "description": "某位专家近年的研究方向关键词。",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "collaborators",
        "description": "某位专家的合作者。",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "works",
        "description": "按主题找论文。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "compare",
        "description": "对比两位专家的方向与产出。",
        "parameters": {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a", "b"]}}},
    {"type": "function", "function": {
        "name": "citations",
        "description": "论文引用关系：它引用了谁 / 谁引用了它（可选两跳）。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "hops": {"type": "integer"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "用户透露了值得长期记住的个人信息（方向/偏好/限制/单位）时调用。",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string",
                    "enum": ["topic", "field", "constraint", "preference", "org"]},
            "value": {"type": "string"}},
            "required": ["key", "value"]}}},
]

_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}

SYSTEM = (
    "你是 ResearchScout，一个科研专家检索助手。你可以调用工具，也可以记住用户的长期偏好。\n"
    "工作方式：\n"
    "1. 先看【长期记忆】和【本会话摘要】，理解用户是谁、在找什么、之前查到哪一步；\n"
    "2. 需要事实就调工具，不要凭空编造人名、论文名或数字；\n"
    "3. 用户透露新的个人信息（换方向、加限制、换单位）时调用 remember；\n"
    "4. 证据足够就停止调用，直接中文作答，并写清依据（人名/论文名）；\n"
    "5. 库里没有证据时明确说不知道，不要用猜测填空；\n"
    "6. 回答简洁，先给结论再给依据。"
)

_REFUSAL_MARKERS = ("没有找到", "未找到", "没有检索到", "无法确定", "不存在",
                    "不知道", "证据不足", "无法判断", "查不到", "无法推荐",
                    "无法回答", "没有可信", "未检索到", "未找到", "不推荐")


def _looks_refusal(text: str) -> bool:
    return any(m in (text or "")[:200] for m in _REFUSAL_MARKERS)


def build_system_prompt(uid: str, sid: str, ep_hint: str = "") -> tuple[str, dict]:
    """拼 system prompt：人设 + 长期记忆 + 本会话摘要 + RSI 策略片段。"""
    hint = memory_recall.build_hint(uid, "") if (uid or "").strip() else {}
    summary_text = session_memory.summary(sid) if sid else ""
    blocks = [SYSTEM]
    if hint.get("facts"):
        facts = "；".join(f"{f.get('key')}={f.get('value')}" for f in hint["facts"][:6])
        blocks.append("【你的长期记忆】" + facts)
    sess = session_memory.prompt_block(sid, summary_text) if sid else ""
    if sess:
        blocks.append(sess)
    if ep_hint:
        blocks.append(ep_hint)
    policy_prompt = (rsi.active_policy() or {}).get("plan_prompt") or ""
    if policy_prompt:
        blocks.append("【策略】" + str(policy_prompt)[:400])
    return "\n\n".join(blocks), hint


def _truncate(obj, limit: int = 1600) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…(截断)"


def _citations(evidence: list[dict]) -> list[dict]:
    out, seen = [], set()
    for ev in evidence:
        for it in (ev.get("items") or [])[:5]:
            if it.get("pid") and ("p", it["pid"]) not in seen:
                seen.add(("p", it["pid"]))
                out.append({"kind": "person", "id": it["pid"],
                            "label": it.get("name_zh") or it.get("name") or str(it["pid"])})
            if it.get("wid") and ("w", it["wid"]) not in seen:
                seen.add(("w", it["wid"]))
                out.append({"kind": "work", "id": it["wid"],
                            "label": (it.get("title") or "")[:80]})
    return out


def _run_tool(name: str, args: dict, uid: str, sid: str) -> dict:
    from app.agent import _run_tool as kb_run
    from app.plan_llm import to_call

    if name == "remember":
        key = str(args.get("key") or "").strip()
        val = str(args.get("value") or "").strip()
        if not key or not val:
            return {"tool": "remember", "ok": False, "error": "key/value 为空"}
        r = user_memory.add_fact(uid, key, val, source="agent")
        return {"tool": "remember", "ok": True, "id": r["id"], "key": key, "value": val}
    mapped = to_call({"tool": name, "args": args}, args.get("query") or "")
    if not mapped:
        return {"tool": name, "error": "参数不合法"}
    if mapped["tool"] == "find_expert":
        mapped["args"]["sid"] = sid or ""
        hint = memory_recall.build_hint(uid, str(args.get("query") or "")) if uid else {}
        if hint:
            mapped["args"]["hint"] = hint
    return kb_run(mapped)


def run(query: str, uid: str = "", sid: str = "", max_steps: int = MAX_STEPS,
        budget_usd: float = BUDGET_USD, episode_hint: bool = True) -> dict:
    """执行一次 Agent 会话。未配置 LLM 时返回 {"ok": False, "reason": ...} 由上层降级。"""
    if not llm.configured():
        return {"ok": False, "reason": "未配置 DEEPSEEK_API_KEY"}
    ep_hint = episodes.hint(query) if episode_hint else ""
    system, hint = build_system_prompt(uid, sid, ep_hint)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": query}]
    evidence: list[dict] = []
    steps: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    cost = 0.0
    calls = 0
    t0 = time.perf_counter()

    def _finish(text: str, reason: str) -> dict:
        text = (text or "").strip()
        # 守卫：所有工具都没返回条目 → 一律按"库里没有"处理，
        # 避免模型用常识把"没查到"包装成一个像答案的段落。
        has_items = any((ev.get("items") or []) for ev in evidence)
        if not has_items:
            text = "知识库里没有找到能支撑这个问题的资料。"
            return {"ok": True, "answer": text, "refused": True,
                    "citations": [], "evidence": evidence, "steps": steps,
                    "usage": usage, "cost_usd": round(cost, 6),
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "engine": "llm", "memory": hint or None, "finish_reason": "no_evidence"}
        refused = (not text) or _looks_refusal(text)
        if not text:
            text = "知识库里没有找到能支撑这个问题的资料。"
            refused = True
        return {"ok": True, "answer": text, "refused": refused,
                "citations": _citations(evidence), "evidence": evidence,
                "steps": steps, "usage": usage, "cost_usd": round(cost, 6),
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "engine": "llm", "memory": hint or None, "finish_reason": reason}

    for _ in range(max_steps):
        if calls >= MAX_CALLS or cost > budget_usd:
            break
        res = llm.chat_with_tools(messages, TOOL_SCHEMAS)
        if not res.get("ok"):
            return {"ok": False, "reason": res.get("error") or "LLM 调用失败"}
        u = res.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        cost += float(res.get("cost_usd") or 0.0)
        msg = res.get("message") or {}
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            return _finish(msg.get("content") or "", "answer")

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name")
            raw = fn.get("args") or fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                args = {}
            calls += 1
            started = time.perf_counter()
            if name not in _TOOL_NAMES:
                out = {"error": f"未知工具 {name}"}
            else:
                try:
                    out = _run_tool(name, args, uid, sid)
                    if out.get("tool") != "remember":
                        evidence.append(out)
                except Exception as exc:
                    out = {"error": f"{type(exc).__name__}: {exc}"}
            steps.append({"tool": name, "args": args,
                          "count": out.get("count"),
                          "ms": round((time.perf_counter() - started) * 1000, 1),
                          "error": out.get("error")})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": _truncate({
                                 "tool": name,
                                 "count": out.get("count"),
                                 "error": out.get("error"),
                                 "items": [{k: v for k, v in it.items()
                                            if k in ("pid", "wid", "name", "name_zh",
                                                     "title", "year", "cites", "inst")}
                                           for it in (out.get("items") or [])[:5]]})})

    # 步数/预算用尽：强制收尾（不再给工具）
    messages.append({"role": "user", "content":
                     "现在基于以上工具结果写最终答案，不要再调用工具；证据不足就明说不知道。"})
    fin = llm.chat_text(messages)
    if fin.get("ok"):
        u = fin.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        cost += float(fin.get("cost_usd") or 0.0)
        return _finish(fin.get("text") or "", "forced")
    return _finish("", "llm_error")


_QUESTION = ("谁", "哪些", "哪个", "找", "推荐", "怎么", "如何", "吗", "?", "？",
             "who", "which", "find", "recommend", "how", "what")


def _is_disclosure(query: str) -> bool:
    """判断这句是不是"自我介绍"而不是提问（"我研究帕金森" vs "谁研究帕金森"）。"""
    q = (query or "").strip()
    if not q:
        return False
    if any(w in q.lower() for w in _QUESTION):
        return False
    try:
        return bool(memory_extract.extract(q))
    except Exception:
        return False


def _ack_disclosure(query: str, uid: str, sid: str, facts: list[dict]) -> dict:
    """自述：写记忆 + 直接确认，不跑工具（避免把"我研究X"当成检索问题拒答）。"""
    written = []
    for f in facts:
        try:
            if f["op"] == "expire":
                user_memory.expire(uid, f["key"])
            else:
                user_memory.add_fact(uid, f["key"], f["value"], source="agent")
                written.append(f["value"])
        except Exception:
            pass
    if sid:
        try:
            session_memory.append(sid, "user", query)
            session_memory.append(sid, "assistant", "已记住：" + "、".join(written))
        except Exception:
            pass
    desc = "、".join(written) or "你的偏好"
    return {"ok": True, "answer": f"已记住：{desc}。后续检索会默认按这个方向来，需要我推荐几位专家吗？",
            "refused": False, "citations": [], "evidence": [], "steps": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "cost_usd": 0.0,
            "ms": 0.0, "engine": "llm", "memory": {"facts": written},
            "finish_reason": "disclosure"}


def chat(query: str, uid: str = "", sid: str = "") -> dict:
    """对外入口：跑一次 Agent，并写回短期/长期记忆。"""
    # 自述句（"我研究帕金森"）不当作检索问题：直接记忆 + 确认
    if uid and _is_disclosure(query):
        try:
            facts = memory_extract.extract(query)
        except Exception:
            facts = []
        if facts:
            return _ack_disclosure(query, uid, sid, facts)
    result = run(query, uid=uid, sid=sid)
    if not result.get("ok"):
        return result

    # 库里没有**相关**专家 → 去 OpenAlex 扩充，然后重试一次（只扩一次）
    _need_expand = bool(result.get("refused"))
    _topic = ""
    try:
        from app import facets, topic_match
        _topic = (facets.parse(query).get("topic") or "").strip()
        if _topic and not _need_expand:
            _pids = [int(it["pid"]) for ev in (result.get("evidence") or [])
                     for it in (ev.get("items") or []) if it.get("pid")]
            _need_expand = not topic_match.matches(_topic, _pids)
    except Exception:
        pass
    if _need_expand and (os.environ.get("ST_AUTO_EXPAND", "0") == "1"):
        try:
            from app import expand
            topic = _topic
            if topic and len(topic) >= 2:
                exp = expand.expand_topic(topic)
                result["expansion"] = exp
                if exp.get("ok") and (exp.get("pids") or []):
                    retry = run(query, uid=uid, sid=sid)
                    if retry.get("ok") and not retry.get("refused"):
                        retry["expansion"] = exp
                        retry["expanded_retry"] = True
                        result = retry
        except Exception as exc:
            result["expansion"] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    # 成功的链路写入共享经验（跨用户复用）
    try:
        eid = episodes.record(query, result, uid=uid)
        if eid:
            result["episode_id"] = eid
    except Exception:
        pass
    if result.get("memory") and not result.get("refused"):
        try:
            _n = memory_recall.note(result["memory"])
            if _n and _n not in result["answer"]:
                result["answer"] = _n + chr(10) + result["answer"]
        except Exception:
            pass
    # 短期记忆：写入本轮，超阈值压缩摘要
    if sid:
        try:
            session_memory.append(sid, "user", query)
            session_memory.append(sid, "assistant", result["answer"][:1200])
            session_memory.maybe_summarize(sid)
        except Exception:
            pass
    # 长期记忆：规则抽取（模型也可能已调用 remember 工具）
    if (uid or "").strip():
        try:
            extracted = memory_extract.apply(uid, query, sid=sid)
            result["memory_write"] = extracted
        except Exception:
            pass
        try:
            user_memory.add_event(uid, sid, query, result.get("engine") or "llm",
                                  "refused" if result.get("refused") else "answered",
                                  meta={"calls": len(result.get("steps") or []),
                                        "cost": result.get("cost_usd")})
        except Exception:
            pass
    return result


def enabled() -> bool:
    """默认启用 LLM Agent；ST_CHAT_ENGINE=classic 时回退旧路径。"""
    if (os.environ.get("ST_CHAT_ENGINE") or "").strip().lower() == "classic":
        return False
    return llm.configured()
