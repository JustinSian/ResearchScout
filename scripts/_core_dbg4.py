import os
from app.db import connect
q="Gaucher disease"
import re
from app.agent import _fts_safe
toks=[t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", _fts_safe(q)) if len(t) > 3][:4]
like=" OR ".join(["lower(w.title) LIKE ?"]*len(toks))
args=[f"%{t}%" for t in toks]
conn=connect()
sql=f"""SELECT wa.pid, COUNT(*) AS hits FROM work_authors wa JOIN works w ON w.wid=wa.wid
        JOIN people p ON p.pid=wa.pid
        WHERE wa.position=0 AND p.status='published' AND ({like})
        GROUP BY wa.pid HAVING hits >= ? ORDER BY hits DESC LIMIT ?"""
try:
    rows=conn.execute(sql, (*args, 1, 10)).fetchall()
    print("rows:", len(rows))
except Exception as e:
    print("ERR:", type(e).__name__, e)
# 不带 HAVING 的对照
sql2=f"""SELECT wa.pid, COUNT(*) AS hits FROM work_authors wa JOIN works w ON w.wid=wa.wid
        JOIN people p ON p.pid=wa.pid
        WHERE wa.position=0 AND p.status='published' AND ({like})
        GROUP BY wa.pid ORDER BY hits DESC LIMIT ?"""
try:
    rows=conn.execute(sql2, (*args, 10)).fetchall()
    print("no-having rows:", len(rows), [tuple(r) for r in rows[:3]])
except Exception as e:
    print("ERR2:", type(e).__name__, e)
conn.close()
