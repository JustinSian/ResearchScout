#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
export OMP_NUM_THREADS=4
PY=.venv/bin/python

echo "== 生成 gold set =="
"$PY" -m app.build_gold 2>&1 | tail -3
echo
echo "== 评测 5 种配置（100 条查询 × 5 = 500 次检索，含编码） =="
time "$PY" -m app.evaluate 2>&1 | tail -22
