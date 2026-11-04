from app import agent_llm, user_memory, memory_extract
def countries(r):
    out=[]
    for ev in (r.get("evidence") or []):
        for it in (ev.get("items") or []):
            out.append(it.get("country"))
    return out
print("== 记忆-国家约束 ==")
uid="wk-us"; user_memory.clear(uid)
print("extract:", memory_extract.apply(uid, "我只要美国的机构", sid="wk1"))
r=agent_llm.chat("推荐几个呼吸科专家", uid=uid, sid="wk1")
print("countries:", countries(r))
print("memory:", r.get("memory"))
print("answer:", (r.get("answer") or "")[:140].replace(chr(10)," "))
print("== 记忆-最近在做 ==")
uid2="wk-recent"; user_memory.clear(uid2)
print("extract:", memory_extract.apply(uid2, "我最近在做肺纤维化", sid="wk2"))
r2=agent_llm.chat("推荐几个专家", uid=uid2, sid="wk2")
print("steps:", [ (s.get("tool"), s.get("args")) for s in (r2.get("steps") or []) ])
print("answer:", (r2.get("answer") or "")[:140].replace(chr(10)," "))
