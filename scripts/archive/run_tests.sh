#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY=.venv/bin/python
echo "== 安装 pytest（复用 venv） =="
"$PY" -m pip install -q pytest 2>&1 | tail -1
echo "== 跑单测 =="
"$PY" -m pytest tests/ -q 2>&1 | tail -8
echo
echo "== API 现状 =="
curl -s -m 8 http://127.0.0.1:8020/stats; echo
