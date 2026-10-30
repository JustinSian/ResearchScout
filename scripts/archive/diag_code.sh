#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
echo "== 服务器上 fetch_pages 的实际代码 =="
sed -n '/def fetch_pages/,/^def run/p' app/ingest_works.py | head -45
echo
echo "== 单页直连测试（用同一份代码路径） =="
PYTHONPATH="$PWD" .venv/bin/python - <<'PY'
import json
from app.ingest_works import fetch_pages
try:
    for i, w in enumerate(fetch_pages(1, 200, log=print)):
        if i == 0:
            print("  第一篇:", (w.get("display_name") or "")[:60])
        if i >= 2:
            break
except Exception as exc:
    print("  失败:", type(exc).__name__, str(exc)[:200])
PY
