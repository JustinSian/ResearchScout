#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
echo "== 安装 langgraph（pip 走 pypi） =="
.venv/bin/pip install -q "langgraph>=0.2" 2>&1 | tail -3
.venv/bin/python - <<'PY'
import langgraph, langchain_core
print("  langgraph", getattr(langgraph, "__version__", "ok"))
print("  langchain_core", getattr(langchain_core, "__version__", "ok"))
from langgraph.graph import StateGraph, START, END
print("  StateGraph ok")
PY
