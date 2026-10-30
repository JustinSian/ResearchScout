cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 提交前检查 =="
.venv/bin/python -m pytest tests -q 2>&1 | tail -1
.venv/bin/python -m compileall -q app >/dev/null 2>&1 && echo "  compileall ok"
git add -A
git commit -q -m "test: 测试扩到 42 项 + README 补质量门禁与 AMiner 数据模型对照

- 新增 tests/test_pipeline.py：融合函数、judge 规则、调度降级、记忆失效、审计落库、去重
- 修 search.py 重复 conn.close()
- README：42 项单测/覆盖率/CI 表；AMiner 28 接口对照（缺 venue 实体与机构消歧）
- 新增 .env.example 与 scripts/dev.sh"
echo "== 提交历史 =="
git log --oneline | head -4
echo "== 仓库 =="
echo "  文件: $(git ls-files | wc -l)  体积: $(du -sh .git | cut -f1)"