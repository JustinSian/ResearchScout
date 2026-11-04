from app import agent_llm
for q in ("介绍一下钟南山","钟南山的研究方向"):
    r=agent_llm.chat(q, uid="rc", sid="rc-"+str(hash(q)))
    print(q, "refused=", r.get("refused"), "steps=", [s["tool"] for s in r.get("steps") or []])
    print("  answer:", (r.get("answer") or "")[:200].replace(chr(10)," "))
