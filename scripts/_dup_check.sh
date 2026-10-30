cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python - <<'PY'
from app import institutions as inst
print("== 近似重复候选（修正后） ==")
for d in inst.near_duplicates(8):
    print(f"  {d['jaccard']}  [{d['relation_guess']:<12}] {d['a'][:34]}  ‖  {d['b'][:34]}")
PY