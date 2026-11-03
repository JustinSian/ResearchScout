from app import memory_extract, user_memory
from app.agent_llm import _SELF_Q, _memory_answer, _is_disclosure
uid="ma-dbg"
user_memory.clear(uid)
q_setup="我的研究领域是肿瘤免疫"
print("extract:", memory_extract.extract(q_setup))
print("apply:", memory_extract.apply(uid, q_setup, sid="s1"))
print("facts:", user_memory.facts(uid))
q="我的研究领域是什么"
print("_SELF_Q match:", bool(_SELF_Q.search(q)))
print("_is_disclosure:", _is_disclosure(q))
r=_memory_answer(q, uid, "s1")
print("memory_answer:", r)
