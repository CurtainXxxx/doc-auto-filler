"""高校教务办公数字员工 - 多Agent协作系统（知识提取→填充→生成）

架构：StateGraph 3节点流水线
  用户消息 → 知识提取Agent → 填充Agent → 生成Agent → 回复

每个Agent输出带标签（[知识提取Agent]/[填充Agent]/[生成Agent]），
评审可直观看到多Agent协作过程。
"""

import os
import json
from typing import Annotated
from dotenv import load_dotenv

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = os.path.join(_project_root, ".env")
if not os.path.isfile(_env_path):
    _env_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), ".env")
load_dotenv(_env_path, override=True)

from langchain.agents.middleware import AgentMiddleware
from langchain.messages import ToolMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AnyMessage
from coze_coding_utils.runtime_ctx.context import default_headers
from storage.memory.memory_saver import get_memory_saver


# ── State ──
class MultiAgentState(MessagesState):
    """多Agent共享状态，继承MessagesState的消息累积机制"""
    remaining_steps: int = 0


# ── Middleware ──
class ToolErrorHandler(AgentMiddleware):
    """工具调用错误处理：捕获异常并返回友好错误消息"""

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except Exception as e:
            return ToolMessage(
                content=f"工具调用出错: {str(e)}",
                tool_call_id=request.tool_call["id"],
            )

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as e:
            return ToolMessage(
                content=f"工具调用出错: {str(e)}",
                tool_call_id=request.tool_call["id"],
            )


class SanitizeBeforeLLM(AgentMiddleware):
    """在消息进入LLM前清理过长的工具输出，防止token溢出"""

    def before_model(self, state, runtime):
        msgs = state.get("messages", [])
        sanitized = []
        for m in msgs:
            if hasattr(m, "content") and isinstance(m.content, str) and len(m.content) > 8000:
                sanitized.append(m.model_copy(update={"content": m.content[:8000] + "\n...(truncated)"}))
            else:
                sanitized.append(m)
        return {"messages": sanitized}

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


# ── LLM 构建 ──
def _build_llm():
    """构建 LLM 实例：优先外部 API，fallback 平台内置模型"""
    external_key = os.getenv("EXTERNAL_LLM_API_KEY")
    if external_key:
        return ChatOpenAI(
            model=os.getenv("EXTERNAL_LLM_MODEL", "deepseek-chat"),
            api_key=external_key,
            base_url=os.getenv("EXTERNAL_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            temperature=0.3,
            streaming=True,
        )

    cfg_path = os.path.join(_project_root, "config", "agent_llm_config.json")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "config", "agent_llm_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    model_name = cfg.get("model", "doubao-seed-1-6-251015")
    return ChatOpenAI(
        model=model_name,
        api_key=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        base_url=os.getenv("ARK_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")),
        temperature=0.3,
        streaming=True,
        default_headers=default_headers() or {},
    )


# ── 工具导入 ──
def _load_tools():
    """延迟导入所有工具，避免循环依赖"""
    from tools.edu_report_tool import (
        list_templates, analyze_report_template, analyze_uploaded_template,
        init_form_filling, update_form_fields, get_form_status,
        generate_form_document, generate_edu_report, generate_from_template,
    )
    from tools.knowledge_tool import parse_knowledge_file, extract_facts
    from tools.old_report_extractor import extract_from_old_report, prefill_from_old_report, get_fill_checklist
    from tools.prefill_tool import prefill_from_knowledge, prefill_from_multiple_knowledge

    return {
        # 知识提取
        "extract_from_old_report": extract_from_old_report,
        "parse_knowledge_file": parse_knowledge_file,
        "extract_facts": extract_facts,
        "prefill_from_old_report": prefill_from_old_report,
        "get_fill_checklist": get_fill_checklist,
        # 填充
        "list_templates": list_templates,
        "analyze_report_template": analyze_report_template,
        "analyze_uploaded_template": analyze_uploaded_template,
        "init_form_filling": init_form_filling,
        "update_form_fields": update_form_fields,
        "get_form_status": get_form_status,
        "prefill_from_knowledge": prefill_from_knowledge,
        "prefill_from_multiple_knowledge": prefill_from_multiple_knowledge,
        # 生成
        "generate_form_document": generate_form_document,
        "generate_edu_report": generate_edu_report,
        "generate_from_template": generate_from_template,
    }


# ── 图构建 ──
def build_agent(ctx=None) -> CompiledStateGraph:
    """构建多Agent协作图（知识提取→填充→生成）"""
    llm = _build_llm()
    tools = _load_tools()
    middleware = [ToolErrorHandler(), SanitizeBeforeLLM()]
    checkpointer = get_memory_saver()

    cfg_path = os.path.join(_project_root, "config", "agent_llm_config.json")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "config", "agent_llm_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # ── 知识提取 Agent ──
    knowledge_agent = create_react_agent(
        model=llm,
        tools=[
            tools["extract_from_old_report"],
            tools["parse_knowledge_file"],
            tools["extract_facts"],
            tools["prefill_from_old_report"],
            tools["get_fill_checklist"],
        ],
        prompt=cfg.get("knowledge_sp", cfg.get("sp", "")),
        state_schema=MultiAgentState,
        name="knowledge_extraction",
    )

    # ── 填充 Agent ──
    filling_agent = create_react_agent(
        model=llm,
        tools=[
            tools["list_templates"],
            tools["analyze_report_template"],
            tools["analyze_uploaded_template"],
            tools["init_form_filling"],
            tools["update_form_fields"],
            tools["get_form_status"],
            tools["prefill_from_knowledge"],
            tools["prefill_from_multiple_knowledge"],
        ],
        prompt=cfg.get("filling_sp", cfg.get("sp", "")),
        state_schema=MultiAgentState,
        name="filling",
    )

    # ── 生成 Agent ──
    generation_agent = create_react_agent(
        model=llm,
        tools=[
            tools["generate_form_document"],
            tools["generate_edu_report"],
            tools["generate_from_template"],
            tools["analyze_report_template"],
        ],
        prompt=cfg.get("generation_sp", cfg.get("sp", "")),
        state_schema=MultiAgentState,
        name="generation",
    )

    # ── 构建 StateGraph 流水线 ──
    builder = StateGraph(MultiAgentState)
    builder.add_node("knowledge_extraction", knowledge_agent)
    builder.add_node("filling", filling_agent)
    builder.add_node("generation", generation_agent)

    builder.add_edge(START, "knowledge_extraction")
    builder.add_edge("knowledge_extraction", "filling")
    builder.add_edge("filling", "generation")
    builder.add_edge("generation", END)

    return builder.compile(checkpointer=checkpointer)
