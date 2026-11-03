from app import memory_extract, user_memory, agent_llm
from app.memory_recall import build_hint
uid="dbg-hard-1"
user_memory.clear(uid)
for q in ["我研究帕金森", "我现在改做阿尔茨海默了"]:
    r=memory_extract.apply(uid, q, sid="dbg-s1")
    print("extract:", q, "->", r)
print("facts:", user_memory.facts(uid))
print("hint:", build_hint(uid, "") )
r=agent_llm.chat("我现在研究什么方向？", uid=uid, sid="dbg-s1")
print("refused:", r.get("refused"), "| memory:", bool(r.get("memory")))
print("answer:", (r.get("answer") or "")[:160].replace(chr(10)," "))
print("---- 约束用例 ----")
uid2="dbg-hard-2"; user_memory.clear(uid2)
print("extract:", memory_extract.apply(uid2, "我只找中国的专家", sid="dbg-s2"))
print("facts:", user_memory.facts(uid2))
r2=agent_llm.chat("推荐几个呼吸科专家", uid=uid2, sid="dbg-s2")
print("answer2:", (r2.get("answer") or "")[:160].replace(chr(10)," "))
