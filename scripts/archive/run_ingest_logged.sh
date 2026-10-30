#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"
LOG=logs/ingest_$(date +%Y%m%d_%H%M%S).log
echo "日志: $LOG"
"$PY" -m app.ingest_works > "$LOG" 2>&1
echo "退出码: $?"
echo "== 最后 18 行 =="
tail -18 "$LOG"
echo "== 出现的 HTTP 错误行 =="
grep -nE "HTTP [0-9]{3}|失败|RuntimeError" "$LOG" | head -10
echo "== 统计 =="
"$PY" - <<'PY'
from app.db import connect, stats
print(stats(connect()))
PY
