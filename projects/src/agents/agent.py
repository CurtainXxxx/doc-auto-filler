"""高校教务办公数字员工 - 多Agent协作系统（Router + 条件路由）

架构：Router 调度 + 条件边
  START → Router → 知识提取Agent → 填充Agent → 生成Agent → END
  Router 根据对话状态跳过已完成的阶段，避免无关 Agent 响应。

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
from langchain.messages import ToolMessage, AIMessage, SystemMessage
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


# ── 阶段检测 ──
def _has_stage_marker(messages: list, marker: str) -> bool:
    """检查消息历史中是否存在指定阶段标记"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            if marker in m.content:
                return True
    return False


def _has_facts(messages: list) -> bool:
    """检查知识提取是否完成（存在 [FACTS] 标签或知识提取Agent的总结输出）"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            content = m.content
            # 检查 [FACTS] 标签
            if '[FACTS]' in content and '[/FACTS]' in content:
                return True
            # 检查知识提取Agent的完成标记
            if '[知识提取完成]' in content:
                return True
            # 检查 extract_facts 工具的成功返回
            if '"fact_count"' in content and '"facts"' in content:
                return True
    return False


def _has_fields_filled(messages: list) -> bool:
    """检查字段填充是否完成（存在 [FIELDS] 标签或填充Agent的完成标记）"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            content = m.content
            if '[FIELDS]' in content and '[/FIELDS]' in content:
                return True
            if '[填充完成]' in content:
                return True
            # 检查 update_form_fields 工具的成功返回（progress_pct > 0）
            if '"progress_pct"' in content and '"success": true' in content:
                return True
    return False


def _has_generation_done(messages: list) -> bool:
    """检查文档生成是否已完成"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            content = m.content
            if '[生成完成]' in content:
                return True
            if 'download_url' in content and '"success": true' in content:
                return True
    return False


# ── Router 节点 ──
def router(state: MultiAgentState) -> dict:
    """路由节点：根据对话状态决定下一个执行的 Agent。
    
    策略：
    - 知识未提取 → knowledge_extraction
    - 知识已提取、未填充 → filling
    - 已填充、未生成 → generation
    - 全部完成 → END
    """
    messages = state.get("messages", [])

    # 检查各阶段完成状态
    knowledge_done = _has_facts(messages)
    filling_done = _has_fields_filled(messages)
    generation_done = _has_generation_done(messages)

    # 决策逻辑
    if not knowledge_done:
        next_node = "knowledge_extraction"
    elif not filling_done:
        next_node = "filling"
    elif not generation_done:
        next_node = "generation"
    else:
        # 全部完成，检查用户最新消息意图
        # 如果用户发了新消息（如补充材料），可能需要重新提取
        last_user_msg_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if hasattr(m, 'type') and m.type == 'human':
                last_user_msg_idx = i
                break
        
        # 检查最后一条用户消息是否在生成完成之后
        if last_user_msg_idx >= 0:
            # 查找生成完成标记的位置
            gen_done_idx = -1
            for i, m in enumerate(messages):
                if hasattr(m, 'content') and isinstance(m.content, str) and '[生成完成]' in m.content:
                    gen_done_idx = i
                    break
            
            # 如果用户消息在生成完成之后，说明是新请求
            if gen_done_idx >= 0 and last_user_msg_idx > gen_done_idx:
                # 新请求：检查是否包含新材料
                last_msg = messages[last_user_msg_idx]
                content = last_msg.content if hasattr(last_msg, 'content') else ""
                # 如果包含文件/材料关键词，重新走知识提取
                if any(kw in content for kw in ['文件', '知识', '材料', '报告', '上传', '.txt', '.docx']):
                    return {"messages": []}  # 不修改状态，路由到知识提取
                # 否则直接到生成（用户可能想重新生成或修改）
                return {"messages": []}
        
        next_node = "__end__"

    return {"messages": []}


def route_from_router(state: MultiAgentState) -> str:
    """从 router 出发的条件边"""
    messages = state.get("messages", [])

    knowledge_done = _has_facts(messages)
    filling_done = _has_fields_filled(messages)
    generation_done = _has_generation_done(messages)

    if not knowledge_done:
        return "knowledge_extraction"
    elif not filling_done:
        return "filling"
    elif not generation_done:
        return "generation"
    else:
        # 检查是否有新的用户消息（生成后的追加请求）
        last_user_msg_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if hasattr(m, 'type') and m.type == 'human':
                last_user_msg_idx = i
                break
        
        gen_done_idx = -1
        for i, m in enumerate(messages):
            if hasattr(m, 'content') and isinstance(m.content, str) and '[生成完成]' in m.content:
                gen_done_idx = i
        
        if gen_done_idx >= 0 and last_user_msg_idx > gen_done_idx:
            # 新请求：根据内容决定路由
            last_msg = messages[last_user_msg_idx]
            content = last_msg.content if hasattr(last_msg, 'content') else ""
            if any(kw in content for kw in ['生成', '导出', '下载', '输出']):
                return "generation"
            elif any(kw in content for kw in ['修改', '更新', '改一下', '调整']):
                return "filling"
            else:
                return "filling"  # 默认回填充阶段处理后续交互
        
        return "__end__"


def route_after_knowledge(state: MultiAgentState) -> str:
    """知识提取完成后的路由"""
    return "filling"


def route_after_filling(state: MultiAgentState) -> str:
    """填充完成后的路由"""
    messages = state.get("messages", [])
    if _has_generation_done(messages):
        return "__end__"
    return "generation"


# ── 图构建 ──
def build_agent(ctx=None) -> CompiledStateGraph:
    """构建多Agent协作图（Router + 条件路由）"""
    llm = _build_llm()
    tools = _load_tools()
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

    # ── 构建 StateGraph（Router + 条件路由）──
    builder = StateGraph(MultiAgentState)

    # 添加节点
    builder.add_node("router", router)
    builder.add_node("knowledge_extraction", knowledge_agent)
    builder.add_node("filling", filling_agent)
    builder.add_node("generation", generation_agent)

    # START → router
    builder.add_edge(START, "router")

    # router → 条件路由到各 Agent 或 END
    builder.add_conditional_edges(
        "router",
        route_from_router,
        {
            "knowledge_extraction": "knowledge_extraction",
            "filling": "filling",
            "generation": "generation",
            "__end__": END,
        },
    )

    # 知识提取 → 填充（总是继续到填充阶段）
    builder.add_conditional_edges(
        "knowledge_extraction",
        route_after_knowledge,
        {
            "filling": "filling",
            "__end__": END,
        },
    )

    # 填充 → 生成 或 END
    builder.add_conditional_edges(
        "filling",
        route_after_filling,
        {
            "generation": "generation",
            "__end__": END,
        },
    )

    # 生成 → END
    builder.add_edge("generation", END)

    return builder.compile(checkpointer=checkpointer)
