from app.agent import tool_find_expert, _is_concept_query
tests = [
 "Find researchers actively working on Gastroenterology",
 "Find researchers actively working on Quasar",
 "Find researchers actively working on Autism",
 "找做 Gastroenterology 的专家",
 "recommend experts in gastroenterology with h-index above 30",
]
for q in tests:
    r = tool_find_expert(q, limit=5)
    print(f"{q[:52]:<54} concept={_is_concept_query(q)!s:<5} mode={r.get('mode')} n={r.get('count')}")
