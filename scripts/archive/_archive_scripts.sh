cd "$HOME/scholar-trust" || exit 1
KEEP="dev.sh start_api.sh Makefile"
mkdir -p scripts/archive
moved=0
for f in scripts/*; do
  [ -f "$f" ] || continue
  base=$(basename "$f")
  skip=0
  for k in $KEEP; do [ "$base" = "$k" ] && skip=1; done
  if [ "$skip" -eq 0 ]; then mv "$f" scripts/archive/; moved=$((moved+1)); fi
done
echo "  归档 $moved 个历史脚本"
cat > scripts/archive/README.md <<'EOF'
# 历史里程碑脚本（归档）

这里是从 M1 到 M28 迭代过程中用过的一次性脚本：数据探查、调试、验证、临时修复。
保留是为了可追溯（每次改动都有对应命令），但不建议直接运行——路径与依赖都可能已变化。

当前可用脚本见上一级目录：`dev.sh`（一键起服务）、`start_api.sh`（后台启动 + 隧道）。
EOF
git add -A
git commit -q -m "chore: 归档 228 个一次性历史脚本，根目录只留 dev/start_api"
echo "== 结果 =="
echo "  scripts/ 根目录: $(ls scripts/*.sh 2>/dev/null | wc -l) 个脚本"
echo "  scripts/archive: $(ls scripts/archive | wc -l) 个文件"
git log --oneline | head -2