#!/usr/bin/env bash
set -euo pipefail

# 基于脚本位置定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "[coze-preview-build] Installing dependencies..."
uv sync

# 重建 langchain 兼容 shim（cozeloop 使用旧导入路径，沙箱重置会清空）
echo "[coze-preview-build] Rebuilding langchain compat shims..."
mkdir -p /usr/local/lib/python3.12/dist-packages/langchain/callbacks
mkdir -p /usr/local/lib/python3.12/dist-packages/langchain/schema

cat > /usr/local/lib/python3.12/dist-packages/langchain/callbacks/__init__.py << 'SHIM_EOF'
from langchain_core.callbacks import (
    BaseCallbackManager, CallbackManager, CallbackManagerForChainRun,
    CallbackManagerForLLMRun, CallbackManagerForToolRun, Callbacks,
    BaseCallbackHandler, AsyncCallbackHandler, dispatch_custom_event,
)
__all__ = ["BaseCallbackManager","CallbackManager","CallbackManagerForChainRun",
    "CallbackManagerForLLMRun","CallbackManagerForToolRun","Callbacks",
    "BaseCallbackHandler","AsyncCallbackHandler","dispatch_custom_event"]
SHIM_EOF

cat > /usr/local/lib/python3.12/dist-packages/langchain/callbacks/base.py << 'SHIM_EOF'
from langchain_core.callbacks import (
    BaseCallbackHandler, AsyncCallbackHandler, BaseCallbackManager,
    CallbackManager, CallbackManagerForChainRun, CallbackManagerForLLMRun,
    CallbackManagerForToolRun, Callbacks, dispatch_custom_event,
)
__all__ = ["BaseCallbackHandler","AsyncCallbackHandler","BaseCallbackManager",
    "CallbackManager","CallbackManagerForChainRun","CallbackManagerForLLMRun",
    "CallbackManagerForToolRun","Callbacks","dispatch_custom_event"]
SHIM_EOF

cat > /usr/local/lib/python3.12/dist-packages/langchain/schema/__init__.py << 'SHIM_EOF'
from langchain_core.agents import AgentFinish, AgentAction
from langchain_core.outputs import LLMResult
from langchain_core.messages import (
    BaseMessage, HumanMessage, AIMessage, SystemMessage, FunctionMessage, ToolMessage, ChatMessage,
)
from langchain_core.documents import Document
from langchain_core.outputs import Generation, ChatGeneration
from langchain_core.language_models.llms import BaseLLM
from langchain_core.language_models.chat_models import BaseChatModel
__all__ = ["AgentFinish","AgentAction","LLMResult",
    "BaseMessage","HumanMessage","AIMessage","SystemMessage",
    "FunctionMessage","ToolMessage","ChatMessage","Document",
    "Generation","ChatGeneration","BaseLLM","BaseChatModel"]
SHIM_EOF

python3 -c "from langchain.callbacks.base import BaseCallbackHandler; from langchain.schema import AgentFinish; print('[coze-preview-build] Shims OK')"

echo "[coze-preview-build] Done."