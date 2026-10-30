"""启动向量构建（后台可跑）。先建 works（快），再建 people（慢）。"""
#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
export OMP_NUM_THREADS=4
LOG=logs/vectors_$(date +%Y%m%d_%H%M%S).log
echo "日志: $LOG"
.venv/bin/python -m app.build_vectors --kind all > "$LOG" 2>&1
echo "退出码 $?"
tail -6 "$LOG"
