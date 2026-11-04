from collections import Counter
import re
from app.db import connect
c=connect()
# 本地作品覆盖分布
rows=c.execute("""SELECT COUNT(*) AS n FROM (
    SELECT wa.pid, COUNT(*) AS wc FROM work_authors wa GROUP BY wa.pid)""").fetchone()[0]
dist=c.execute("""SELECT CASE WHEN wc=0 THEN '0' WHEN wc=1 THEN '1' WHEN wc<6 THEN '2-5' WHEN wc<20 THEN '6-19' ELSE '20+' END AS bucket, COUNT(*) FROM (
    SELECT wa.pid, COUNT(*) AS wc FROM work_authors wa GROUP BY wa.pid) GROUP BY bucket ORDER BY bucket""").fetchall()
print("有本地作品的人:", rows, "| 分布:", [(r[0],r[1]) for r in dist])

# 为 pid 527 现算 fallback
STOP=set("with from using based their this that these those study research analysis model method system data results patients".split())
pid=527
def term_profile(pid, since, until, top=8):
    rr=c.execute("""SELECT w.title,w.year FROM work_authors wa JOIN works w ON w.wid=wa.wid
                    WHERE wa.pid=? AND w.year BETWEEN ? AND ?""", (pid, since, until)).fetchall()
    ct=Counter()
    for r in rr:
        for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", (r["title"] or "").lower()):
            if w in STOP or len(w)<4: continue
            ct[w]+=1
    return ct.most_common(top)
cites=[r["cites"] for r in c.execute("""SELECT w.cites FROM work_authors wa JOIN works w ON w.wid=wa.wid WHERE wa.pid=?""", (pid,))]
years=[r["year"] for r in c.execute("""SELECT w.year FROM work_authors wa JOIN works w ON w.wid=wa.wid WHERE wa.pid=? AND w.year IS NOT NULL""", (pid,))]
venues=Counter(r["venue"] for r in c.execute("""SELECT w.venue FROM work_authors wa JOIN works w ON w.wid=wa.wid WHERE wa.pid=? AND w.venue IS NOT NULL""", (pid,)))
cites_sorted=sorted(cites, reverse=True)
h=sum(1 for i,v in enumerate(cites_sorted,1) if v>=i)
print("works:", len(cites), "| cites_sum:", sum(cites), "| h_index:", h)
print("first_year:", min(years) if years else None, "| last_year:", max(years) if years else None)
print("top_venues:", [v for v,_ in venues.most_common(3)])
print("recent(2022-2026):", term_profile(pid, 2022, 2026))
print("early(<=2021):", term_profile(pid, 1900, 2021))
c.close()
