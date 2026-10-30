cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app.db import connect
c = connect()
print("works 列:", [r[1] for r in c.execute("PRAGMA table_info(works)")])
r = c.execute("SELECT * FROM works LIMIT 1").fetchone()
print("示例行:", {k: r[k] for k in r.keys()})
PY