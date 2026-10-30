#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
import json
d = json.load(open("/tmp/hf_openapi.json"))
paths = d.get("paths") or {}
for p in sorted(paths):
    methods = ",".join(sorted(m.upper() for m in paths[p]))
    summary = ""
    for m, spec in paths[p].items():
        summary = spec.get("summary") or spec.get("description") or ""
        break
    print(f"  {methods:<10} {p:<46} {summary[:36]}")
PY
