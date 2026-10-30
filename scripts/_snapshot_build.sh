cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python -m app.snapshot --people 60 --works 1200 2>&1 | tail -6
echo
echo "== 用快照启动一次真实查询（ST_DB 指向快照） =="
ST_DB="$HOME/scholar-trust/data/snapshot/demo.db" .venv/bin/python - <<'PY'
import os
print("  DB:", os.environ["ST_DB"])
from app.search import search_people
from app.dossiers import dossier
from app import venues
hits = search_people("钟南山", limit=3)
print("  搜索命中:", [(h[0], round(float(h[1]), 2)) for h in hits])
d = dossier(527)
print("  档案:", (d["name_zh"] if d else None), d["works_loaded"] if d else None)
print("  期刊 top3:", [(v["name"][:26], v["works_count"]) for v in venues.top_venues(3)])
PY