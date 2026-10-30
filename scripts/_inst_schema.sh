cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app.db import connect
c = connect()
print("institutions 列:", [r[1] for r in c.execute("PRAGMA table_info(institutions)")])
print("行数:", c.execute("SELECT COUNT(*) FROM institutions").fetchone()[0])
PY