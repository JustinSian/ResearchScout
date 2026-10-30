#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 某学术图谱 OpenAPI 里的专家相关路径 =="
curl -s -m 25 http://10.x.x.x:<port>/openapi.json -o /tmp/hf_openapi.json
.venv/bin/python - <<'PY'
import json
d = json.load(open("/tmp/hf_openapi.json"))
paths = d.get("paths") or {}
hits = [p for p in paths if any(k in p.lower() for k in ("expert", "scholar", "author"))]
print(f"  总路径 {len(paths)}，专家相关 {len(hits)}")
for p in sorted(hits)[:24]:
    methods = ",".join(sorted(m.upper() for m in paths[p]))
    print(f"   {methods:<8} {p}")
    # 打印第一个 GET 的参数名
    for m, spec in paths[p].items():
        params = [q.get("name") for q in (spec.get("parameters") or [])]
        if params:
            print(f"        参数: {params[:8]}")
        break
PY
