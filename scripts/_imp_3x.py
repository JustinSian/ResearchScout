from app import agent_llm
q="找研究 quantum gravity phenology 的专家"
ok=0
for i in range(3):
    r=agent_llm.chat(q, uid=f"imp{i}", sid=f"imp{i}-s")
    ref=bool(r.get("refused")); ok+=ref
    print(f"run{i}: refused={ref} | {(r.get('answer') or '')[:70].replace(chr(10),' ')}")
print(f"拒答 {ok}/3")
