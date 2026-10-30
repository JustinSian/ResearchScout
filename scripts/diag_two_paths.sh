#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"

echo "== A. 直接调 fetch_pages(1 页) =="
"$PY" - <<'PY'
from app.ingest_works import fetch_pages
try:
    items = list(fetch_pages(1, 200, log=print))
    print("  ok:", len(items))
except Exception as exc:
    print("  失败:", type(exc).__name__, str(exc)[:160])
PY

echo "== B. 走 run(pages=1) =="
"$PY" - <<'PY'
try:
    from app.ingest_works import run
    print("  ", run(pages=1, log=print))
except Exception as exc:
    print("  失败:", type(exc).__name__, str(exc)[:160])
PY
