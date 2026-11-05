import io

P = r"E:\yolov7\researchscout\app\agent_llm.py"
s = io.open(P, encoding="utf-8").read()
NL = chr(10)

# 1) _run_tool 增加 orig 参数与回退逻辑
old_sig = 'def _run_tool(name: str, args: dict, uid: str, sid: str) -> dict:'
new_sig = 'def _run_tool(name: str, args: dict, uid: str, sid: str, orig: str = "") -> dict:'
assert old_sig in s
s = s.replace(old_sig, new_sig, 1)

old_tail = NL.join([
    '    mapped = to_call({"tool": name, "args": args}, args.get("query") or "")',
    '    if not mapped:',
    '        return {"tool": name, "error": "参数不合法"}',
    '    return kb_run(mapped)',
])
new_tail = NL.join([
    '    mapped = to_call({"tool": name, "args": args}, args.get("query") or "")',
    '    if not mapped:',
    '        return {"tool": name, "error": "参数不合法"}',
    '    out = kb_run(mapped)',
    '    # 主题改写回退：模型常把用户的问题改写成同义长句（"考古" → "archaeological',
    '    # excavation carbon dating"），而影子索引按原主题建，改写后可能 0 命中，',
    '    # 表现为"第一问没找到、第二问才对"。这里用**用户原句**清洗出的主题再查一次。',
    '    if name == "find_expert" and not (out.get("items") or []) and orig:',
    '        try:',
    '            from app.expand import clean_topic',
    '            cur = str((mapped.get("args") or {}).get("query") or "")',
    '            alt = clean_topic(orig)',
    '            if len(alt) >= 2 and alt not in cur:',
    '                retry = dict(mapped)',
    '                retry["args"] = {**(mapped.get("args") or {}), "query": alt}',
    '                r2 = kb_run(retry)',
    '                if r2.get("items"):',
    '                    r2["query_used"] = alt',
    '                    r2["fallback_from"] = cur',
    '                    out = r2',
    '        except Exception:',
    '            pass',
    '    return out',
])
assert old_tail in s, "run_tool tail not found"
s = s.replace(old_tail, new_tail, 1)

# 2) 执行工具时把用户原句传进去
old_exec = '                    _out = _run_tool(_name, _args, uid, sid)'
new_exec = '                    _out = _run_tool(_name, _args, uid, sid, orig=query)'
assert old_exec in s, "exec call not found"
s = s.replace(old_exec, new_exec, 1)

# 3) step 里带上实际使用的 query（可追溯）
old_step = NL.join([
    '            return {"tool": _name, "args": _args,',
    '                    "count": _out.get("count"),',
    '                    "ms": round((time.perf_counter() - _t0) * 1000, 1),',
    '                    "error": _out.get("error"), "_out": _out}',
])
new_step = NL.join([
    '            return {"tool": _name, "args": _args,',
    '                    "count": _out.get("count"),',
    '                    "ms": round((time.perf_counter() - _t0) * 1000, 1),',
    '                    "error": _out.get("error"),',
    '                    "query_used": _out.get("query_used"), "_out": _out}',
])
if old_step in s:
    s = s.replace(old_step, new_step, 1)

io.open(P, "w", encoding="utf-8", newline=NL).write(s)
chk = io.open(P, encoding="utf-8").read()
print("orig param:", "orig: str = \"\"" in chk,
      "| fallback:", "主题改写回退" in chk,
      "| exec passes orig:", "orig=query" in chk)
