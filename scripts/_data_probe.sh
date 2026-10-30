cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app.db import connect
c = connect()
def one(sql, args=()):
    return c.execute(sql, args).fetchone()[0]
print("== 机构 ==")
print("  orgs 行数:", one("SELECT COUNT(*) FROM orgs"))
print("  person_org 行数:", one("SELECT COUNT(*) FROM person_org"))
print("  people.last_inst 去重数:", one("SELECT COUNT(DISTINCT last_inst) FROM people WHERE last_inst IS NOT NULL"))
print("  top 5 机构（按人数）:")
for r in c.execute("""SELECT o.name, COUNT(*) n FROM person_org po JOIN orgs o ON o.oid=po.oid
                      GROUP BY o.oid ORDER BY n DESC LIMIT 5"""):
    print(f"    {r['n']:>4}  {r['name']}")
print("== 期刊 ==")
print("  works.venue 去重数:", one("SELECT COUNT(DISTINCT venue) FROM works WHERE venue IS NOT NULL"))
print("  top 5 期刊（按论文数）:")
for r in c.execute("""SELECT venue, COUNT(*) n FROM works WHERE venue IS NOT NULL
                      GROUP BY venue ORDER BY n DESC LIMIT 5"""):
    print(f"    {r['n']:>4}  {r['venue'][:60]}")
print("== 是否需要消歧：名字包含同一机构关键词的 top 组 ==")
for kw in ("Guangzhou Medical", "Sun Yat-sen", "State Key Laboratory"):
    n = one("SELECT COUNT(*) FROM orgs WHERE name LIKE ?", (f"%{kw}%",))
    print(f"    {kw!r}: {n} 个 org 记录")
PY