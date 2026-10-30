#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app.db import connect
c = connect()
print("== 库里所有 Hinton ==")
for r in c.execute("SELECT pid,name,works_count,cited_by_count,status,confidence,last_inst "
                   "FROM people WHERE name LIKE '%Hinton%'"):
    print("  ", dict(r))
print("\n== 其他名人抽查 ==")
for name in ("Yoshua Bengio", "Yann LeCun", "Ian Goodfellow", "Fei-Fei Li", "Andrew Ng"):
    rows = c.execute("SELECT name,works_count,cited_by_count,status,confidence "
                     "FROM people WHERE name=?", (name,)).fetchall()
    print(f"  {name:<18}", [dict(r) for r in rows] or "（不在样本内）")
print("\n== 样本内'专家'规模 ==")
for label, sql in [
    ("published", "SELECT COUNT(*) FROM people WHERE status='published'"),
    ("published & works>=2", "SELECT COUNT(*) FROM people WHERE status='published' AND works_count>=2"),
    ("published & works>=3", "SELECT COUNT(*) FROM people WHERE status='published' AND works_count>=3"),
    ("works>=2 (任意状态)", "SELECT COUNT(*) FROM people WHERE works_count>=2"),
]:
    print(f"  {label:<22}", c.execute(sql).fetchone()[0])
PY
