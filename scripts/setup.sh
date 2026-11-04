#!/usr/bin/env bash
# ResearchScout 一键启动：clone 后跑这个脚本即可进入可用状态。
# 用法：bash scripts/setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== ResearchScout 安装 =="

# 1. Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python 3.11"
  exit 1
fi
PY=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  python $PY"

# 2. 虚拟环境 + 依赖
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  echo "  创建 .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "  依赖已安装"

# 3. .env
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "  已生成 .env（请填入 LLM_API_KEY）"
else
  echo "  .env 已存在，跳过"
fi

# 4. 演示数据
if [ -f "data/snapshot/demo.db" ]; then
  echo "  演示库 data/snapshot/demo.db 就绪"
else
  echo "  ⚠️  缺少演示库 data/snapshot/demo.db（应随仓库提供）"
fi

# 5. 交叉编码器（可选）
if [ -z "${ST_RERANK_DIR:-}" ] && [ -d "$HOME/med-compliance-agent/data/models/bge-reranker-onnx" ]; then
  echo "  检测到 bge-reranker 模型，将启用交叉编码器"
else
  echo "  ℹ️  未检测到 bge-reranker（可选）：无它也能跑，但'库里到底有没有'判定精度下降"
  echo "     如需启用：设置 ST_RERANK_DIR 指向含 model.onnx/model_int8.onnx 的目录"
fi

echo ""
echo "== 完成。启动服务 =="
echo "  ST_DB=data/snapshot/demo.db .venv/bin/uvicorn app.api:app --host 0.0.0.0 --port 8020"
echo "  打开 http://127.0.0.1:8020"
echo ""
echo "  下一步：编辑 .env，至少填入 LLM_API_KEY（支持任意 OpenAI 兼容端点）"
