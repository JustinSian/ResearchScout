#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
PY="$HOME/scholar-trust/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "== 建 venv（继承 anaconda3 的包） =="
  "$HOME/anaconda3/bin/python" -m venv --system-site-packages "$HOME/scholar-trust/.venv"
  "$HOME/scholar-trust/.venv/bin/pip" install -q --upgrade pip 2>&1 | tail -1
  echo "== 装 onnxruntime（M2 向量检索用） =="
  "$HOME/scholar-trust/.venv/bin/pip" install -q onnxruntime 2>&1 | tail -2
fi
"$PY" -V
"$PY" -c "import fastapi, uvicorn, numpy, onnxruntime; print('deps ok:', fastapi.__version__, onnxruntime.__version__)"
