"""One model configuration for agents, knowledge extraction, and prefill."""
import os
from urllib.parse import urlsplit
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import runtime  # Load project .env before reading configuration.

def build_model(*, temperature=0.3, streaming=False):
    """Use an explicitly configured OpenAI-compatible endpoint (including local servers)."""
    base_url = os.getenv("EXTERNAL_LLM_BASE_URL")
    model = os.getenv("EXTERNAL_LLM_MODEL")
    key = os.getenv("EXTERNAL_LLM_API_KEY")
    if not base_url or not model or not key:
        raise ValueError("请在 projects/.env 配置 EXTERNAL_LLM_BASE_URL、EXTERNAL_LLM_MODEL、EXTERNAL_LLM_API_KEY；本地无认证服务可使用非空占位值。")
    # DeepSeek enables thinking by default. ChatOpenAI does not replay its
    # reasoning_content field across tool calls, which causes HTTP 400 on the
    # next agent turn. Use the supported non-thinking tool-call protocol; do
    # not send this provider-specific field to other compatible endpoints.
    provider_options = {}
    if urlsplit(base_url).hostname == "api.deepseek.com":
        provider_options["extra_body"] = {"thinking": {"type": "disabled"}}
    return ChatOpenAI(model=model, api_key=key, base_url=base_url,
                      temperature=temperature, streaming=streaming,
                      max_tokens=4096, timeout=600, max_retries=2,
                      **provider_options)

def call_model(system_prompt, user_prompt):
    """Return text consistently for string and structured message content."""
    content = build_model(temperature=0.1).invoke([
        SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)
    ]).content
    if isinstance(content, str):
        return content
    return " ".join(item.get("text", "") for item in content if isinstance(item, dict))
