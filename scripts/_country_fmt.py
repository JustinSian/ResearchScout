from app.db import connect
c=connect()
print("country 样例:", [tuple(r) for r in c.execute("SELECT country, COUNT(*) FROM people WHERE country IS NOT NULL GROUP BY country ORDER BY 2 DESC LIMIT 6")])
c.close()
