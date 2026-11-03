from app.db import connect
c=connect()
print("== 有中文名的人（前 10，按被引）==")
for r in c.execute("SELECT pid,name_zh,cited_by_count FROM people WHERE name_zh IS NOT NULL AND name_zh!='' ORDER BY cited_by_count DESC LIMIT 10"):
    print("  ", tuple(r))
print("== 英文名人名是否在库 ==")
for n in ("Robert Tibshirani","Trevor Hastie","Yoshua Bengio","Geoffrey Hinton","Jennifer Doudna"):
    r=c.execute("SELECT pid,name,cited_by_count FROM people WHERE name LIKE ? ORDER BY cited_by_count DESC LIMIT 1", (f"%{n}%",)).fetchone()
    print(f"  {n}: {dict(r) if r else None}")
print("== 同一话题下被引最高的两个人（可做对比/关系用例）==")
rows=c.execute("""SELECT p1.pid AS a, p1.name AS na, p2.pid AS b, p2.name AS nb, t.topic
                  FROM people p1 JOIN people p2 ON p1.pid < p2.pid
                  JOIN (SELECT pid, topics FROM people) x ON x.pid=p1.pid,
                  json_each(x.topics) t
                  WHERE p1.cited_by_count > 50000 AND p2.cited_by_count > 50000
                  LIMIT 0""").fetchall()
print("  （跳过复杂 SQL，直接用被引 top 20 里找同话题对）")
top=c.execute("SELECT pid,name,topics,cited_by_count FROM people ORDER BY cited_by_count DESC LIMIT 40").fetchall()
import json
seen={}
pairs=[]
for r in top:
    try: tags=json.loads(r["topics"] or "[]")
    except: tags=[]
    for t in tags[:3]:
        if t in seen and seen[t] != r["pid"]:
            pairs.append((t, seen[t], r["name"], r["cited_by_count"]))
        seen.setdefault(t, r["pid"])
for p in pairs[:5]: print("  ", p)
c.close()
