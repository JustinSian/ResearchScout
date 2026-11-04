from app.db import connect
from app.agent import tool_profile, tool_research_area
c=connect()
r=c.execute("SELECT pid,name,name_zh,topics,works_count,cited_by_count,inst_id,first_author_works FROM people WHERE pid=527").fetchone()
print("people row:", dict(r) if r else None)
print("work_authors 行数:", c.execute("SELECT COUNT(*) FROM work_authors WHERE pid=527").fetchone()[0])
print("works 关联:", c.execute("SELECT COUNT(*) FROM work_authors wa JOIN works w ON w.wid=wa.wid WHERE wa.pid=527").fetchone()[0])
c.close()
print("profile:", str(tool_profile(527))[:300])
print("research_area:", str(tool_research_area("钟南山"))[:300])
