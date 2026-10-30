cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 全量测试 =="
.venv/bin/python -m pytest tests -q 2>&1 | tail -2
echo "== 提交 =="
git add -A
git commit -q -m "feat: MCP server（stdio JSON-RPC，6 个工具）+ README 用法

不依赖 mcp SDK，直接实现 initialize/tools/list/tools/call/ping；
测试 50 -> 55"
git log --oneline | head -3
echo "  文件: $(git ls-files | wc -l)  体积: $(du -sh .git | cut -f1)"
echo "== 服务仍在 =="
URL=$(cat logs/public_url.txt)
curl -s -o /dev/null -w "  / → %{http_code}\n" -m 30 "$URL/"