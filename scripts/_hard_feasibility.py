from app.db import connect
c=connect()
print("== 虚构人名是否在库（应为 0）==")
for n in ("王小明","张伟明","赵大勇","李建国","陈晓东"):
    r=c.execute("SELECT COUNT(*) FROM people WHERE name LIKE ? OR name_zh LIKE ?", (f"%{n}%", f"%{n}%")).fetchone()[0]
    print(f"  {n}: {r}")
print("== 虚构主题标签是否在库（应为 0）==")
for t in ("quantum gravity phenology","blockchain neuroscience","CRISPR gastronomy","astrobiology poetry"):
    r=c.execute("SELECT COUNT(*) FROM people WHERE topics LIKE ?", (f"%{t}%",)).fetchone()[0]
    print(f"  {t}: {r}")
print("== 候选对比人（应存在且被引高）==")
for n in ("钟南山","李兰娟","施一公","饶毅","张伯礼","陈薇","王辰","张文宏"):
    r=c.execute("SELECT pid,name,name_zh,cited_by_count FROM people WHERE name LIKE ? OR name_zh=? ORDER BY cited_by_count DESC LIMIT 1", (f"%{n}%", n)).fetchone()
    print(f"  {n}: {dict(r) if r else None}")
c.close()
