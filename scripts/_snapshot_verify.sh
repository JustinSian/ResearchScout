cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python -m app.snapshot --people 60 --works 1200 2>&1 | tail -4
echo
echo "== 引用完整性 + 图谱 =="
ST_DB="$HOME/scholar-trust/data/snapshot/demo.db" .venv/bin/python - <<'PY'
from app.db import connect
c = connect()
orphan = c.execute("SELECT COUNT(*) FROM work_authors WHERE pid NOT IN (SELECT pid FROM people)").fetchone()[0]
print("  孤儿作者行:", orphan)
print("  论文-作者关联:", c.execute("SELECT COUNT(*) FROM work_authors").fetchone()[0])
from app.search import graph_neighbors
g = graph_neighbors(527, depth=1)
print("  图谱节点:", len(g["nodes"]), "边:", len(g["edges"]))
from app.dossiers import dossier
print("  档案作品数:", dossier(527)["works_loaded"])
PY