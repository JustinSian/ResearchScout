cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 服务是否还活着 =="
URL=$(cat logs/public_url.txt)
curl -s -o /dev/null -w "  / → %{http_code}\n" -m 30 "$URL/"
echo "== 工程文件清单 =="
for f in README.md LICENSE .gitignore requirements.txt pyproject.toml .env.example .github/workflows/ci.yml scripts/dev.sh tests/test_core.py tests/test_pipeline.py; do
  [ -e "$f" ] && echo "  ✓ $f" || echo "  ✗ $f"
done
echo "== 单测 =="
.venv/bin/python -m pytest tests -q 2>&1 | tail -1
echo "== 覆盖率（核心模块） =="
.venv/bin/python -m coverage run --source=app -m pytest tests -q >/dev/null 2>&1
.venv/bin/python -m coverage report 2>/dev/null | tail -1
echo "== git =="
git log --oneline | wc -l | xargs echo "  提交数:"
git ls-files | wc -l | xargs echo "  跟踪文件:"