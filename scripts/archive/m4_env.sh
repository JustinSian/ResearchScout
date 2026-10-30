#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
.venv/bin/python - <<'PY'
import importlib
for m in ("langgraph", "langchain_core", "pydantic", "sse_starlette"):
    try:
        mod = importlib.import_module(m)
        print(f"  {m}: {getattr(mod, '__version__', 'ok')}")
    except Exception as exc:
        print(f"  {m}: 缺失 ({type(exc).__name__})")
PY
echo "== PyPI =="
curl -s -o /dev/null -m 10 -w "  pypi %{http_code}\n" https://pypi.org/simple/
