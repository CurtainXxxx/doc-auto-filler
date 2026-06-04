#!/bin/bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PORT=5000

usage() {
  echo "用法: $0 -p <端口>"
}

while getopts "p:h" opt; do
  case "$opt" in
    p)
      PORT="$OPTARG"
      ;;
    h)
      usage
      exit 0
      ;;
    \?)
      echo "无效选项: -$OPTARG"
      usage
      exit 1
      ;;
  esac
done

export PORT

# 激活 .venv（devbox 环境），deploy 无 .venv 则跳过
if [ -f "${PROJECT_DIR}/.venv/bin/activate" ]; then
  source "${PROJECT_DIR}/.venv/bin/activate"
fi

python src/main.py -m http -p "$PORT"