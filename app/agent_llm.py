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
import re
import time
from concurrent.futures import ThreadPoolExecutor

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
    "2.1 提前规划：确定人名后，profile / research_area / works 应当同一轮并行调用，\n"
    "    不要一个一个串行来（每轮都是一次模型往返，串行会让用户多等好几秒）；\n"
    "3. 用户透露新的个人信息（换方向、加限制、换单位）时调用 remember；\n"
    "4. 证据足够就停止调用，直接中文作答，并写清依据（人名/论文名）；\n"
    "5. 库里没有证据时明确说不知道，不要用猜测填空；\n"
    "6. 回答简洁，先给结论再给依据。"
)

_REFUSAL_MARKERS = ("没有找到", "没找到", "找不到", "未能找到", "未找到",
                    "没有检索到", "未检索到", "检索不到", "无法确定", "不存在",
                    "不知道", "证据不足", "无法判断", "查不到", "无法推荐",
                    "无法回答", "没有可信", "不推荐", "没有真正", "未能检索")

# 只靠固定短语不够：模型会把同一句拒答写成"没有找到…"、"库里没有研究…的专家"、
# "查不到…"等十几种说法（实测同一条件下判否率只有 3/5）。补一条"否定词 + 主题词"。
_NEG_TOPIC = re.compile(
    r"(库里|系统|数据库|资料库|专家库)?\s*(没有|没|未|无法|不能)"
    r"[^。；;]{0,40}(研究|专家|学者|匹配|命中|核心|结果|检索|推荐|找到|相关)")


def _looks_refusal(text: str) -> bool:
    head = (text or "")[:200]
    if any(m in head for m in _REFUSAL_MARKERS):
        return True
    return bool(_NEG_TOPIC.search(head))


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


# 工具结果回传给模型时保留的字段。之前只留 pid/wid/name/title/year/cites/inst，
# 导致 research_area 的 recent/early/top_venues、profile 的 topics/country 全被丢掉，
# 模型只能看到"姓名+被引"，于是回答"没有研究方向字段"（实测踩过）。
_RESULT_FIELDS = ("pid", "wid", "name", "name_zh", "title", "year", "cites", "inst",
                  "country", "country_src", "topics", "topics_zh", "h_index", "works",
                  "works_count", "recent", "early", "top_venues", "first_year",
                  "last_year", "n_matched_works", "match", "source", "orcid",
                  "last_inst")


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
        # 模型有时给 "direction"/"研究方向" 之类，映射到合法 key，而不是报错
        _alias = {"direction": "topic", "研究方向": "topic", "领域": "field",
                  "field_of_study": "field", "限制": "constraint", "偏好": "preference",
                  "单位": "org", "机构": "org", "国家": "country"}
        key = _alias.get(key, key)
        if key not in ("topic", "field", "constraint", "preference", "org", "country"):
            key = "topic"
        if not val:
            return {"tool": "remember", "ok": False, "error": "value 为空"}
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
        budget_usd: float = BUDGET_USD, episode_hint: bool = True,
        on_step=None, on_delta=None) -> dict:
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
        # 一轮里的多个工具调用**并行执行**（模型可同时要 profile/research_area/works），
        # 本地工具各开各的连接，串行纯属浪费；顺序与 tool_call_id 必须保持一一对应。
        prepared = []
        for call in tool_calls:
            fn = call.get("function") or {}
            raw = fn.get("args") or fn.get("arguments") or "{}"
            try:
                cargs = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except Exception:
                cargs = {}
            prepared.append((call, fn.get("name"), cargs))

        def _exec_one(item):
            _call, _name, _args = item
            _t0 = time.perf_counter()
            if _name not in _TOOL_NAMES:
                _out = {"error": f"未知工具 {_name}"}
            else:
                try:
                    _out = _run_tool(_name, _args, uid, sid)
                except Exception as _exc:
                    _out = {"error": f"{type(_exc).__name__}: {_exc}"}
            return {"tool": _name, "args": _args,
                    "count": _out.get("count"),
                    "ms": round((time.perf_counter() - _t0) * 1000, 1),
                    "error": _out.get("error"), "_out": _out}

        # 多工具同轮的价值是**减少 LLM 往返**（每轮 1–3 秒），不是并发跑工具。
        # 实测并发执行会让 SQLite 抢锁：单个工具 23ms → 671ms，反而更慢。
        # 所以默认本地串行执行（几十毫秒），只在显式开启 ST_PARALLEL_EXEC=1 时并发。
        if len(prepared) > 1 and os.environ.get("ST_PARALLEL_EXEC", "0") == "1":
            with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as _pool:
                results = list(_pool.map(_exec_one, prepared))
        else:
            results = [_exec_one(x) for x in prepared]

        calls += len(results)
        for (call, _name, _args), r in zip(prepared, results):
            out = r.pop("_out")
            if out.get("tool") != "remember":
                evidence.append(out)
            steps.append(r)
            if on_step:
                try:
                    on_step(r)
                except Exception:
                    pass
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": _truncate({
                                 "tool": _name,
                                 "count": out.get("count"),
                                 "error": out.get("error"),
                                 "items": [{k2: v2 for k2, v2 in it.items()
                                            if k2 in _RESULT_FIELDS}
                                           for it in (out.get("items") or [])[:5]]})})

    # 步数/预算用尽：强制收尾（不再给工具）
    messages.append({"role": "user", "content":
                     "现在基于以上工具结果写最终答案，不要再调用工具；证据不足就明说不知道。"})
    # 最终作答走流式：首 token 1 秒内到达，而不是整段算完再吐
    if on_delta:
        fin = llm.chat_stream(messages, on_delta=on_delta)
    else:
        fin = llm.chat_text(messages)
    if fin.get("ok"):
        u = fin.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        cost += float(fin.get("cost_usd") or 0.0)
        return _finish(fin.get("text") or "", "forced")
    return _finish("", "llm_error")


_QUESTION = ("谁", "哪些", "哪个", "找", "推荐", "怎么", "如何", "吗", "?", "？",
             "什么", "多少", "哪", "呢", "几位", "who", "which", "find",
             "recommend", "how", "what")


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



# ---- 纯记忆问答 / 澄清：不走检索（工具返回空时会被"无证据守卫"误判为拒答）----
_SELF_Q = re.compile(r"(我|我的).{0,8}(研究|方向|领域|记得|之前|说过|做的是|是什么)")
_GENERIC_REQ = re.compile(
    r"^(?:请|帮我|给我)?\s*(?:推荐|找|要|来)\s*(?:几个|一些|几位|批)?\s*"
    r"(?:专家|学者|人|老师)\s*[。？?]?$")
_KEY_LABEL = {"topic": "研究方向", "field": "研究领域", "constraint": "限制条件",
              "preference": "偏好", "role": "身份", "org": "单位", "country": "国家"}


def _memory_answer(query: str, uid: str, sid: str) -> dict | None:
    """"我现在研究什么方向？"这类问题应该由记忆回答，而不是去检索。"""
    if not uid or not _SELF_Q.search(query or ""):
        return None
    try:
        facts = user_memory.facts(uid)
    except Exception:
        return None
    if not facts:
        return None
    parts = [f"{_KEY_LABEL.get(f['key'], f['key'])}={f['value']}" for f in facts]
    text = "根据你的长期记忆：" + "；".join(parts) + "。"
    return {"ok": True, "answer": text, "refused": False, "citations": [],
            "evidence": [], "steps": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "cost_usd": 0.0, "ms": 0.0, "engine": "llm", "memory": None,
            "finish_reason": "memory_answer"}


def _clarify(query: str, uid: str) -> dict | None:
    """没有任何领域信息的"推荐几个专家"应该追问，而不是硬推一批人。"""
    if not _GENERIC_REQ.match((query or "").strip()):
        return None
    try:
        if uid and user_memory.facts(uid):
            return None          # 有记忆主题时，交给记忆补全
    except Exception:
        pass
    return {"ok": True,
            "answer": "你想找哪个领域的专家？告诉我方向（例如「帕金森」「图神经网络」），"
                      "或者补充国家/机构类型等条件，我再帮你找。",
            "refused": False, "citations": [], "evidence": [], "steps": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "cost_usd": 0.0,
            "ms": 0.0, "engine": "llm", "memory": None, "finish_reason": "clarify"}


def _expansion_message(report: dict) -> str:
    """把补库结果变成一句给用户看的公示：补了 / 没补 / 为什么没补。"""
    topic = report.get("topic") or "该主题"
    if not report.get("attempted"):
        return f"【库内补充】未执行：{report.get('reason') or '自动扩充未开启'}。"
    if not report.get("ok"):
        return (f"【库内补充】库里没有「{topic}」的专家；尝试补充失败："
                f"{report.get('reason') or '未知原因'}。")
    ppl = int(report.get("added_people") or 0)
    wks = int(report.get("added_works") or 0)
    if ppl > 0:
        tail = "，并已用新数据重试" if report.get("answered_after_retry") else ""
        return (f"【库内补充】库里原来没有「{topic}」的专家，已从 OpenAlex 补充 "
                f"{ppl} 位学者、{wks} 篇论文并入库{tail}。")
    if "24" in (report.get("reason") or ""):
        return f"【库内补充】「{topic}」在 24 小时内已补充过，本次未重复采集。"
    return (f"【库内补充】已尝试为「{topic}」补充，但没有找到可靠的匹配"
            f"（标题未命中），未入库。")


def chat(query: str, uid: str = "", sid: str = "", on_step=None,
         on_delta=None, on_reset=None) -> dict:
    """对外入口：跑一次 Agent，并写回短期/长期记忆。"""
    # 自述句（"我研究帕金森"）不当作检索问题：直接记忆 + 确认
    if uid and _is_disclosure(query):
        try:
            facts = memory_extract.extract(query)
        except Exception:
            facts = []
        if facts:
            return _ack_disclosure(query, uid, sid, facts)
    # 纯记忆问题（"我现在研究什么方向？"）直接由记忆回答，避免被无证据守卫判成拒答
    mem_ans = _memory_answer(query, uid, sid)
    if mem_ans:
        return mem_ans
    # 无任何领域信息的泛请求 → 追问
    clar = _clarify(query, uid)
    if clar:
        return clar
    result = run(query, uid=uid, sid=sid, on_step=on_step, on_delta=on_delta)
    if not result.get("ok"):
        return result

    # 库里没有**相关**专家 → 去 OpenAlex 扩充，然后重试一次（只扩一次）
    _need_expand = bool(result.get("refused"))
    _topic = ""
    _person = None
    try:
        from app import facets, topic_match
        # 主题优先从**原句**清洗：facets.parse 对中文切词很差
        # （"我想了解人工智能…" 会被切成 "我想了解 工智能"，丢了"人"）
        from app.expand import clean_topic as _ct0
        _topic = (_ct0(query) or (facets.parse(query).get("topic") or "")).strip()
        # 人名/关系/论文类问题不触发扩充（否则会拿整句去搜 OpenAlex）
        if any(w in query for w in ("合作者", "论文", "引用", "对比", "比较",
                                    "介绍", "是谁", "导师", "邮箱", "代表作")):
            _need_expand = False
        # 问的是"某个具体的人"？人名不是主题，拿去搜 OpenAlex 必然 0 命中。
        # 走"按作者 id 补全作品"的专用路径，而不是主题扩充。
        try:
            from app.agent import _resolve_person as _rp
            _person = _rp(None, _topic)
            if _person:
                _need_expand = False
        except Exception:
            _person = None
        # 用翻译后的英文主题做一致性判断，否则中文问句 vs 英文标签永远不匹配、
        # 会反复触发无谓的扩充（实测"建筑"每问一次都当成缺口）
        _match_topic = _topic
        try:
            from app.expand import clean_topic as _ct, to_english as _te
            _match_topic = _te(_ct(_topic)) or _topic
        except Exception:
            pass
        if _match_topic and not _need_expand:
            _pids = [int(it["pid"]) for ev in (result.get("evidence") or [])
                     for it in (ev.get("items") or []) if it.get("pid")]
            _need_expand = not topic_match.matches(_match_topic, _pids)
    except Exception:
        pass
    if _need_expand and (os.environ.get("ST_AUTO_EXPAND", "1") == "1"):
        try:
            from app import expand
            # 主题清洗 + 中文翻译：OpenAlex 是英文库，"我想认识建筑" 搜不到
            display = expand.clean_topic(_topic) or _topic
            search = expand.to_english(display)
            if search and len(search) >= 2:
                exp = expand.expand_topic(search)
                result["expansion"] = exp
                report = {
                    "topic": display,
                    "search_term": search,
                    "attempted": True,
                    "ok": bool(exp.get("ok")),
                    "added_people": int(exp.get("added_people") or 0),
                    "added_works": int(exp.get("added_works") or 0),
                    "reason": exp.get("reason") or "",
                }
                if exp.get("ok") and (exp.get("pids") or []):
                    if on_reset:
                        on_reset()
                    retry = run(query, uid=uid, sid=sid,
                                on_step=on_step, on_delta=on_delta)
                    report["retried"] = True
                    report["answered_after_retry"] = bool(retry.get("ok")
                                                          and not retry.get("refused"))
                    if retry.get("ok") and not retry.get("refused"):
                        retry["expansion"] = exp
                        retry["expanded_retry"] = True
                        retry["expansion_report"] = report
                        result = retry
                result["expansion_report"] = report
        except Exception as exc:
            result["expansion"] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            result["expansion_report"] = {"topic": _topic, "attempted": True, "ok": False,
                                          "reason": f"{type(exc).__name__}: {exc}"}
    elif _need_expand:
        result["expansion_report"] = {"topic": _topic, "attempted": False,
                                      "ok": False, "reason": "自动扩充已关闭"}

    # 具体某位专家：库里作品太少时，按 OpenAlex 作者档案补全（人名不是主题）
    if _person and not result.get("refused"):
        try:
            from app import expand as _exp
            _pid = int(_person["pid"])
            if _exp.count_person_works(_pid) < 20:
                en = _exp.enrich_person(_pid, per_page=50)
                result["person_enrichment"] = en
                if en.get("ok") and en.get("added_works"):
                    if on_reset:
                        on_reset()
                    retry_p = run(query, uid=uid, sid=sid,
                                  on_step=on_step, on_delta=on_delta)
                    if retry_p.get("ok") and not retry_p.get("refused"):
                        retry_p["person_enrichment"] = en
                        result = retry_p
        except Exception as exc:
            result["person_enrichment"] = {"ok": False,
                                           "reason": f"{type(exc).__name__}: {exc}"}

    if result.get("person_enrichment"):
        _en = result["person_enrichment"]
        if _en.get("ok") and _en.get("added_works"):
            _msg = (f"【专家补全】库里「{_en.get('name')}」原有作品偏少，已按 OpenAlex "
                    f"作者档案补全 {_en['added_works']} 篇（现共 {_en.get('total_works')} 篇）。")
        elif not _en.get("ok"):
            _msg = f"【专家补全】尝试补全失败：{_en.get('reason')}。"
        else:
            _msg = ""
        if _msg:
            result["answer"] = ((result.get("answer") or "").rstrip()
                                + chr(10) + chr(10) + _msg)

    # 公示板：把"补库结果"明确告诉用户（补了/没补/为什么没补），不藏在日志里。
    if result.get("expansion_report"):
        msg = _expansion_message(result["expansion_report"])
        if msg:
            result["answer"] = ((result.get("answer") or "").rstrip()
                                + "\n\n" + msg)

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
