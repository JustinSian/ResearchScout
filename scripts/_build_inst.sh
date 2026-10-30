cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app import institutions as inst
print("== 建档 ==")
print(inst.build())
print("== top 机构 ==")
for i in inst.top(6):
    print(f"  {i['people_count']:>4} 人  [{i['kind']:<10}] {i['name'][:52]}")
print("== 近似重复候选（只报告，不自动合并） ==")
dups = inst.near_duplicates(6)
print(f"  共 {len(dups)} 对（展示前 6）")
for d in dups:
    print(f"  {d['jaccard']}  {d['a'][:38]}  ‖  {d['b'][:38]}")
print("== 某专家机构 ==")
print(" ", inst.for_person(527))
PY