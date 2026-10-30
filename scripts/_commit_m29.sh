cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python -m pytest tests -q 2>&1 | tail -1
git add -A
git commit -q -m "feat: 实体层补强（期刊 2314 + 机构 4319），analyst 输出机构与常发期刊

- venues: works.venue 归一为实体表，PubMed 等仓储单列；10,599/11,104 篇挂载
- institutions: 4,320 个 last_inst 收敛为 4,319 个实体，38,663 人挂载
- 近似重复只报告不合并：parent_child / similar / same_tokens_confirm
- 新增 5 个接口：/venues /venue/{vid} /institutions /institutions/duplicates /people/{pid}/affiliation
- 测试 42 -> 50"
echo "== 提交 =="
git log --oneline | head -3
echo "  文件: $(git ls-files | wc -l)"