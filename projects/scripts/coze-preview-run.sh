#!/usr/bin/env bash
set -euo pipefail

# 基于脚本位置定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 显式声明关键环境变量，不依赖平台执行环境继承
export PORT=5000
export COZE_PROJECT_TYPE=agent

# 清理 5000 端口残留进程（绝不碰 9000）
fuser -k 5000/tcp 2>/dev/null || true
sleep 1

echo "[coze-preview-run] Starting FastAPI server on port $PORT..."

exec python src/main.py -m http -p "$PORT"