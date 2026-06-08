"""高校教务办公数字员工 - 多Agent协作系统（Router + 条件路由）

架构：Router 调度 + 条件边
  START → Router → 知识提取Agent → 填充Agent → 生成Agent → END
  Router 根据对话状态跳过已完成的阶段，避免无关 Agent 响应。

每个Agent输出带标签（[知识提取Agent]/[填充Agent]/[生成Agent]），
评审可直观看到多Agent协作过程。
"""

import os
import re
import json
from typing import Annotated
from dotenv import load_dotenv

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = os.path.join(_project_root, ".env")
if not os.path.isfile(_env_path):
    _env_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), ".env")
load_dotenv(_env_path, override=True)

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
MAX_MESSAGES = 40


class MultiAgentState(MessagesState):
    """多Agent共享状态，滑动窗口限制消息数防止token爆炸"""
    messages: Annotated[list[AnyMessage], lambda old, new: add_messages(old, new)[-MAX_MESSAGES:]]
    remaining_steps: int = 0


# ── 消息清理 Hook（替代 AgentMiddleware，create_react_agent 不支持 middleware 参数）──
def _sanitize_messages(state, runtime):
    """pre_model_hook：在消息进入LLM前清理过长的工具输出，防止token溢出"""
    msgs = state.get("messages", [])
    sanitized = []
    for m in msgs:
        if hasattr(m, "content") and isinstance(m.content, str) and len(m.content) > 8000:
            sanitized.append(m.model_copy(update={"content": m.content[:8000] + "\n...(truncated)"}))
        else:
            sanitized.append(m)
    return {"messages": sanitized}


# ── LLM 构建 ──
def _build_llm(ctx=None):
    """构建 LLM 实例：优先外部 API（环境变量 → config fallback），再 fallback 平台内置模型"""
    external_key = os.getenv("EXTERNAL_LLM_API_KEY")

    # Fallback: 从 agent_llm_config.json 读取（用于部署环境无 .env 的情况）
    if not external_key:
        try:
            cfg_path = os.path.join(_project_root, "config", "agent_llm_config.json")
            if not os.path.isfile(cfg_path):
                cfg_path = os.path.join(
                    os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"),
                    "config", "agent_llm_config.json"
                )
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
            ext_cfg = raw_cfg.get("external_llm", {})
            if ext_cfg.get("api_key"):
                external_key = ext_cfg["api_key"]
                # 临时设为环境变量，后续代码（如 prefill 工具）也能用到
                os.environ["EXTERNAL_LLM_API_KEY"] = external_key
                os.environ["EXTERNAL_LLM_BASE_URL"] = ext_cfg.get("base_url", "https://api.deepseek.com/v1")
                os.environ["EXTERNAL_LLM_MODEL"] = ext_cfg.get("model", "deepseek-chat")
        except Exception:
            pass

    if external_key:
        return ChatOpenAI(
            model=os.getenv("EXTERNAL_LLM_MODEL", "deepseek-chat"),
            api_key=external_key,
            base_url=os.getenv("EXTERNAL_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            temperature=0.3,
            max_tokens=4096,
            streaming=True,
            timeout=600,
        )

    cfg_path = os.path.join(_project_root, "config", "agent_llm_config.json")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "config", "agent_llm_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    # Bug 3 修复：config 结构是 {"config": {"model": ..., "temperature": ...}}，不是平铺
    cfg = raw_cfg.get("config", raw_cfg)

    model_name = cfg.get("model", "doubao-seed-1-6-251015")
    temperature = cfg.get("temperature", 0.7)
    timeout = cfg.get("timeout", 600)

    # Bug 2 修复：传入 ctx 生成平台认证头
    headers = default_headers(ctx) if ctx else default_headers() or {}

    return ChatOpenAI(
        model=model_name,
        api_key=os.getenv("ARK_API_KEY", os.getenv("OPENAI_API_KEY", "")),
        base_url=os.getenv("ARK_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")),
        temperature=temperature,
        max_tokens=4096,
        streaming=True,
        timeout=timeout,
        default_headers=headers,
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


# ── 阶段检测（Bug 4 修复：用正则增强匹配，容忍空格/换行/markdown包裹）──

# 匹配 [FACTS]...[/FACTS] 或 [知识提取完成]（容忍前后空格、markdown代码块包裹）
_RE_FACTS_BLOCK = re.compile(r'\[FACTS\].*?\[/FACTS\]', re.DOTALL)
_RE_KNOWLEDGE_DONE = re.compile(r'\[知识提取完成\]')

# 匹配 [FIELDS]...[/FIELDS] 或 [填充完成]
_RE_FIELDS_BLOCK = re.compile(r'\[FIELDS\].*?\[/FIELDS\]', re.DOTALL)
_RE_FILLING_DONE = re.compile(r'\[填充完成\]')

# 匹配 [生成完成] 或 download_url + success
_RE_GENERATION_DONE = re.compile(r'\[生成完成\]')
_RE_DOWNLOAD_SUCCESS = re.compile(r'"download_url".*"success"\s*:\s*true', re.DOTALL)


def _has_facts(messages: list) -> bool:
    """检查知识提取是否完成（正则匹配，容忍格式变化）"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            content = m.content
            if _RE_FACTS_BLOCK.search(content):
                return True
            if _RE_KNOWLEDGE_DONE.search(content):
                return True
            # 检查 extract_facts 工具的成功返回（fact_count > 0）
            import re
            if re.search(r'"fact_count"\s*:\s*[1-9]\d*', content):
                return True
    return False


def _has_fields_filled(messages: list) -> bool:
    """检查字段填充是否完成（正则匹配，容忍格式变化）"""
    for m in messages:
        if hasattr(m, 'content') and isinstance(m.content, str):
            content = m.content
            if _RE_FIELDS_BLOCK.search(content):
                return True
            if _RE_FILLING_DONE.search(content):
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
            if _RE_GENERATION_DONE.search(content):
                return True
            if _RE_DOWNLOAD_SUCCESS.search(content):
                return True
    return False


# ── Router 节点 ──
def router(state: MultiAgentState) -> dict:
    """路由节点：无状态变更，仅作为条件边的起点。实际路由决策在 route_from_router。"""
    return {"messages": []}


def route_from_router(state: MultiAgentState) -> str:
    """从 router 出发的条件边：根据对话状态决定下一个执行的 Agent。

    策略：
    - 知识未提取 → knowledge_extraction
    - 知识已提取、未填充 → filling
    - 已填充、未生成 → generation
    - 全部完成 → END
    """
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
        # 全部完成，检查是否有新的用户消息（生成后的追加请求）
        last_user_msg_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if hasattr(m, 'type') and m.type == 'human':
                last_user_msg_idx = i
                break

        gen_done_idx = -1
        for i, m in enumerate(messages):
            if hasattr(m, 'content') and isinstance(m.content, str) and _RE_GENERATION_DONE.search(m.content):
                gen_done_idx = i

        if gen_done_idx >= 0 and last_user_msg_idx > gen_done_idx:
            last_msg = messages[last_user_msg_idx]
            content = last_msg.content if hasattr(last_msg, 'content') else ""
            # 新文档请求 → 重新走知识提取
            if any(kw in content for kw in ['另一份', '新的', '再来', '下一个', '另外', '再填', '新模板', '新文档', '换个']):
                return "knowledge_extraction"
            elif any(kw in content for kw in ['生成', '导出', '下载', '输出']):
                return "generation"
            elif any(kw in content for kw in ['修改', '更新', '改一下', '调整']):
                return "filling"
            else:
                return "filling"

        return "__end__"


def route_after_knowledge(state: MultiAgentState) -> str:
    """知识提取完成后的路由：有事实数据才进填充，否则结束等用户补充"""
    if _has_facts(state.get("messages", [])):
        return "filling"
    return "__end__"


def route_after_filling(state: MultiAgentState) -> str:
    """填充完成后的路由"""
    messages = state.get("messages", [])
    if _has_generation_done(messages):
        return "__end__"
    return "generation"


# ── 图构建 ──
def build_agent(ctx=None) -> CompiledStateGraph:
    """构建多Agent协作图（Router + 条件路由）"""
    # Bug 2 修复：ctx 传入 _build_llm 用于平台认证
    llm = _build_llm(ctx)
    tools = _load_tools()
    checkpointer = get_memory_saver()

    cfg_path = os.path.join(_project_root, "config", "agent_llm_config.json")
    if not os.path.isfile(cfg_path):
        cfg_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "config", "agent_llm_config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    # Bug 3 修复：从 cfg.config 读取
    cfg = raw_cfg.get("config", raw_cfg)

    # Bug 1 修复：使用 pre_model_hook 做消息清理（create_react_agent 不支持 middleware）
    common_kwargs = dict(
        state_schema=MultiAgentState,
        pre_model_hook=_sanitize_messages,
    )

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
        prompt=raw_cfg.get("knowledge_sp", raw_cfg.get("sp", "")),
        name="knowledge_extraction",
        **common_kwargs,
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
        prompt=raw_cfg.get("filling_sp", raw_cfg.get("sp", "")),
        name="filling",
        **common_kwargs,
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
        prompt=raw_cfg.get("generation_sp", raw_cfg.get("sp", "")),
        name="generation",
        **common_kwargs,
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
