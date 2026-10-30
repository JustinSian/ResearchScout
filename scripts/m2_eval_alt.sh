#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
export OMP_NUM_THREADS=4
PY=.venv/bin/python

echo "== 第一套 gold（institution/topic 真值按被引取 top5） =="
"$PY" -m app.evaluate eval/gold.json 2>&1 | grep -E "^  (bm25|dense|rrf|dbsf)|按查询|^  (配置|bm25_a40|bm25_a80)" | head -14
echo
echo "== 第二套 gold（真值随机抽 8 人，不按被引） =="
"$PY" -m app.build_gold_alt 2>&1 | tail -1
"$PY" -m app.evaluate eval/gold_alt.json 2>&1 | grep -E "^  (bm25|dense|rrf|dbsf)" | head -12
echo
echo "== 两套 gold 的 by-tier（第二套） =="
"$PY" -m app.evaluate eval/gold_alt.json 2>&1 | sed -n '/按查询类型/,$p' | head -8
