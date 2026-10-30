cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
echo "== 测试 =="
.venv/bin/python -m pytest tests -q 2>&1 | tail -1
echo "== 快照是否被 git 看见 =="
git add -A
git status --porcelain | grep snapshot | head -3
echo "== 快照直接用 =="
ST_DB="$HOME/scholar-trust/data/snapshot/demo.db" .venv/bin/python -c "
from app.supervisor import run
r = run('介绍一下钟南山', log=lambda *_: None)
print('  route:', r['route'], '| refused:', r['refused'], '| 引用', len(r['citations']))
print('  A:', r['answer'][:90].replace(chr(10),' '))"
git commit -q -m "feat: 演示数据集快照（6.3MB / 60 人 / 1200 篇），clone 即可跑

- app/snapshot.py 从主库裁剪：只保留有深档案的专家与其论文，重建 FTS
- 修剪孤儿作者行，保证引用完整（0 orphan）
- .gitignore 放行 data/snapshot/demo.db；README 补 ST_DB 用法"
echo "== 提交 =="
git log --oneline | head -3
echo "  文件: $(git ls-files | wc -l)  体积: $(du -sh .git | cut -f1)"