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

# patch: 兼容 cozeloop 新版 LoopTracer.get_callback_handler(client) 不接受 tags 参数
python3 << 'PATCH_EOF'
import sys, os
target = "/usr/local/lib/python3.12/dist-packages/coze_coding_utils/log/loop_trace.py"
if os.path.exists(target):
    with open(target) as f:
        src = f.read()
    # 如果还没打过补丁（旧版代码中调用了 get_callback_handler 含 tags/add_tags_fn 等参数）
    if "add_tags_fn=tracer.get_node_tags" in src:
        import re
        # 替换 init_run_config
        src = re.sub(
            r'def init_run_config\(graph, ctx\):.*?return config',
            '''def init_run_config(graph, ctx):
    tracer = Logger(graph, ctx)
    tracer.on_chain_start = tracer.on_chain_start_graph
    tracer.on_chain_end = tracer.on_chain_end_graph
    trace_callback_handler = LoopTracer.get_callback_handler(cozeloopTracer)
    config = RunnableConfig(callbacks=[tracer, trace_callback_handler])
    return config''',
            src, flags=re.DOTALL
        )
        # 替换 init_agent_config
        src = re.sub(
            r'def init_agent_config\(graph, ctx\):.*?return config',
            '''def init_agent_config(graph, ctx):
    config = RunnableConfig(callbacks=[LoopTracer.get_callback_handler(cozeloopTracer)])
    return config''',
            src, flags=re.DOTALL
        )
        with open(target, 'w') as f:
            f.write(src)
        print("[patch] loop_trace.py patched for cozeloop API compatibility")
    else:
        print("[patch] loop_trace.py already patched, skipping")
PATCH_EOF

echo "[coze-preview-run] Starting FastAPI server on port $PORT..."

exec python src/main.py -m http -p "$PORT"