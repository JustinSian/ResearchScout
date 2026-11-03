from app.agent import _core_experts, _topic_experts
for q in ("Gaucher disease", "Gastroenterology", "Quasar"):
    try:
        c=_core_experts(q, limit=10)
        print(f"{q:<20} core={len(c)}", [x['pid'] for x in c[:3]])
    except Exception as e:
        print(f"{q:<20} ERROR {type(e).__name__}: {e}")
    t=_topic_experts(q, limit=10)
    print(f"{'':<20} topic={len(t)}", [x['pid'] for x in t[:3]])
