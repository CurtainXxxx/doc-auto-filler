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

_workspace = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
load_dotenv(os.path.join(_workspace, ".env"), override=True)

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain.messages import ToolMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import AnyMessage
from coze_coding_utils.runtime_ctx.context import default_headers
from storage.memory.memory_saver import get_memory_saver
from tools.edu_report_tool import (
    generate_edu_report, analyze_report_template, list_templates,
    analyze_uploaded_template, generate_from_template,
    init_form_filling, get_form_status, update_form_fields, generate_form_document,
)
from tools.knowledge_tool import parse_knowledge_file, extract_facts
from tools.prefill_tool import prefill_from_knowledge, prefill_from_multiple_knowledge
from tools.old_report_extractor import (
    extract_from_old_report, prefill_from_old_report, get_fill_checklist,
    inject_form_states as _inject_form_states,
)
from tools.edu_report_tool import _active_form_states

LLM_CONFIG = "config/agent_llm_config.json"
MAX_MESSAGES = 40


def _strip_reasoning(msg):
    """清理 DeepSeek 等模型返回的 reasoning_content，防止多轮对话报错"""
    if not isinstance(msg, AIMessage):
        return msg
    rc = getattr(msg, "reasoning_content", None)
    if not rc:
        return msg
    try:
        delattr(msg, "reasoning_content")
    except Exception:
        pass
    if hasattr(msg, "additional_kwargs") and "reasoning_content" in msg.additional_kwargs:
        msg.additional_kwargs.pop("reasoning_content", None)
    return msg


def _windowed_messages(old, new):
    """滑动窗口: 只保留最近 MAX_MESSAGES 条消息，并清理 reasoning_content"""
    merged = add_messages(old, new)[-MAX_MESSAGES:]
    merged = [_strip_reasoning(m) for m in merged]
    merged = _fix_orphan_tool_messages(merged)
    return merged


def _fix_orphan_tool_messages(messages):
    """删除没有对应 AIMessage.tool_calls 的 ToolMessage，防止 API 400 错误"""
    valid_tool_call_ids = set()
    for m in messages:
        if isinstance(m, AIMessage) and hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                if "id" in tc:
                    valid_tool_call_ids.add(tc["id"])
    result = []
    for m in messages:
        if isinstance(m, ToolMessage):
            if m.tool_call_id not in valid_tool_call_ids:
                continue
        result.append(m)
    return result


class MultiAgentState(MessagesState):
    """多Agent共享状态：消息列表 + 滑动窗口"""
    messages: Annotated[list[AnyMessage], _windowed_messages]


@wrap_tool_call
def handle_tool_errors(request, handler):
    """工具执行错误处理——所有Agent共用"""
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"工具执行出错: ({str(e)})",
            tool_call_id=request.tool_call["id"]
        )


@wrap_tool_call
def sanitize_before_llm(request, handler):
    """发送给LLM前清理孤立ToolMessage——所有Agent共用"""
    if hasattr(request, 'messages') and request.messages:
        valid_ids = set()
        for m in request.messages:
            for tc in (getattr(m, "tool_calls", None) or []):
                valid_ids.add(tc.get("id"))
        request.messages = [
            m for m in request.messages
            if getattr(m, "type", "") != "tool"
            or (getattr(m, "tool_call_id", None) in valid_ids)
        ]
    return handler(request)


def _build_llm(cfg, ctx=None):
    """构建 LLM 实例（支持外部API和平台内置模型）"""
    ext_api_key = os.getenv("EXTERNAL_LLM_API_KEY")
    ext_base_url = os.getenv("EXTERNAL_LLM_BASE_URL")

    if ext_api_key and ext_base_url:
        api_key = ext_api_key
        base_url = ext_base_url
        model = os.getenv("EXTERNAL_LLM_MODEL", "deepseek-chat")
    else:
        api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
        base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
        model = cfg["config"].get("model", "doubao-seed-1-6-251015")

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=cfg["config"].get("temperature", 0.7),
        streaming=True,
        timeout=cfg["config"].get("timeout", 600),
        extra_body=(
            {"thinking": {"type": "disabled"}} if ext_api_key else {
                "thinking": {
                    "type": cfg["config"].get("thinking", "disabled")
                }
            }
        ),
        default_headers=default_headers(ctx) if ctx and not ext_api_key else {},
    )


def build_agent(ctx=None) -> CompiledStateGraph:
    """构建多Agent协作系统

    流水线架构（StateGraph 3节点）:
      START → knowledge_extraction（知识提取Agent）
           → filling（填充Agent）
           → generation（生成Agent）
           → END

    每个Agent是独立的 create_agent，配不同的 system_prompt 和工具集。
    固定顺序连接，不需要 Supervisor 路由。
    """
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    llm = _build_llm(cfg, ctx)
    checkpointer = get_memory_saver()
    middleware = [handle_tool_errors, sanitize_before_llm]

    # 注入共享状态，让 old_report_extractor 能访问 edu_report_tool 的会话
    _inject_form_states(_active_form_states)

    # ── 知识提取 Agent ──
    # 职责：从用户上传的材料中提取结构化事实
    # 工具：旧报告提取、知识文件解析、事实提取、数据准备清单
    knowledge_agent = create_agent(
        model=llm,
        system_prompt=cfg.get("knowledge_sp", cfg.get("sp", "")),
        tools=[
            extract_from_old_report, parse_knowledge_file, extract_facts,
            prefill_from_old_report, get_fill_checklist,
        ],
        state_schema=MultiAgentState,
        middleware=middleware,
    )

    # ── 填充 Agent ──
    # 职责：将事实表匹配到模板字段，批量填入，回显驱动前端预览
    # 工具：模板分析、表单初始化、字段更新、AI预填、状态查询
    filling_agent = create_agent(
        model=llm,
        system_prompt=cfg.get("filling_sp", cfg.get("sp", "")),
        tools=[
            list_templates, analyze_report_template, analyze_uploaded_template,
            init_form_filling, update_form_fields, get_form_status,
            prefill_from_knowledge, prefill_from_multiple_knowledge,
        ],
        state_schema=MultiAgentState,
        middleware=middleware,
    )

    # ── 生成 Agent ──
    # 职责：用户确认后生成最终 docx 文档，返回下载链接
    # 工具：表单文档生成、内置模板生成、上传模板生成、模板分析
    generation_agent = create_agent(
        model=llm,
        system_prompt=cfg.get("generation_sp", cfg.get("sp", "")),
        tools=[
            generate_form_document, generate_edu_report, generate_from_template,
            analyze_report_template,
        ],
        state_schema=MultiAgentState,
        middleware=middleware,
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
