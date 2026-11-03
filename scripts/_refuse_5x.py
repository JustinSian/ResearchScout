from app import agent_llm
q="找研究 quantum gravity phenology 的专家"
ok=0
for i in range(5):
    r=agent_llm.chat(q, uid=f"rf{i}", sid=f"rf{i}-s")
    ref=bool(r.get("refused")); ok+=ref
    print(f"run{i}: refused={ref} | {(r.get('answer') or '')[:60].replace(chr(10),' ')}")
print(f"拒答率 {ok}/5")
