#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
echo "== 日志文件 =="
ls -lt logs/ | head -5
NEW=$(ls -t logs/ingest_*.log 2>/dev/null | head -1)
echo "最新: $NEW"
echo "== 该日志的 HTTP 行与页进度 =="
grep -nE "HTTP [0-9]{3}|第 [0-9]+/" "$NEW" | head -12
echo "== 该日志尾部 =="
tail -6 "$NEW"
echo
echo "== 当前代码的重试等待值（应为 4/8/12/16/20） =="
grep -n "wait = " app/ingest_works.py
echo "== 当前库 =="
PYTHONPATH="$PWD" .venv/bin/python -c "
from app.db import connect, stats; print(stats(connect()))"
