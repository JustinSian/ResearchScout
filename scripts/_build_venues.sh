cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app import venues
print("== 建档 ==")
print(venues.build())
print("== top 期刊 ==")
for v in venues.top_venues(6):
    print(f"  {v['works_count']:>4}  {v['name'][:52]}")
print("== 仓储类（应被标为 repository） ==")
for v in venues.top_venues(4, kind="repository"):
    print(f"  {v['works_count']:>4}  {v['name'][:40]}  kind={v['kind']}")
print("== 某专家常发期刊 ==")
for pv in venues.person_venues(527, 5):
    print(f"  {pv['n']:>3} 篇  {pv['name'][:50]}  (最近 {pv['last_year']})")
PY