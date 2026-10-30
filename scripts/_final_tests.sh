cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 全量测试 =="
.venv/bin/python -m pytest tests -q 2>&1 | tail -3
echo "== 覆盖率 =="
.venv/bin/python -m coverage run --source=app -m pytest tests -q >/dev/null 2>&1
.venv/bin/python -m coverage report 2>/dev/null | tail -4
echo "== 覆盖率（纯逻辑模块） =="
.venv/bin/python -m coverage report 2>/dev/null | grep -E "judge|supervisor|search|memory|hooks|skills|websearch|narrate|onboard" | head -10