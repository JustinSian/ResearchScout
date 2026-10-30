#!/usr/bin/env bash
# 一键起服务（开发用）。生产请用 systemd / supervisor 托管。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "[1/4] 建虚拟环境"
  python3 -m venv .venv
fi

echo "[2/4] 安装依赖"
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  echo "[3/4] 生成 .env（从 .env.example 复制，记得改 ST_API_KEY）"
  cp .env.example .env
else
  echo "[3/4] .env 已存在，跳过"
fi

echo "[4/4] 初始化数据库并启动（:8020）"
export PYTHONPATH="$PWD"
.venv/bin/python -m app.init_db
mkdir -p logs
exec .venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port 8020
