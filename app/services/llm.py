from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import get_settings

_REQUEST_API_KEY: ContextVar[str] = ContextVar("request_llm_api_key", default="")


def _message_to_text(message: Any) -> str:
    """把模型消息对象提取成纯文本。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def set_request_api_key(api_key: str | None) -> Token[str]:
    """为当前请求上下文写入 API Key。"""
    normalized = str(api_key or "").strip()
    return _REQUEST_API_KEY.set(normalized)


def reset_request_api_key(token: Token[str]) -> None:
    """恢复当前请求上下文中的 API Key。"""
    _REQUEST_API_KEY.reset(token)


def get_request_api_key() -> str:
    """读取当前请求上下文中的 API Key。"""
    return str(_REQUEST_API_KEY.get() or "").strip()


def get_chat_model(
    temperature: float | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """创建当前配置对应的聊天模型实例。"""
    settings = get_settings()
    resolved_api_key = (
        str(api_key or "").strip()
        or get_request_api_key()
        or settings.llm_api_key
    )
    if not resolved_api_key:
        raise RuntimeError("LLM API key is required. Paste it into the page or send X-LLM-API-Key.")

    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "api_key": resolved_api_key,
    }

    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url

    if settings.llm_max_tokens > 0:
        kwargs["max_tokens"] = settings.llm_max_tokens

    return ChatOpenAI(**kwargs)


def run_completion(
    system_prompt: str,
    user_prompt: str,
    temperature: float | None = None,
) -> str:
    """执行一次基础模型补全调用。"""
    model = get_chat_model(temperature=temperature)
    messages = [SystemMessage(system_prompt), HumanMessage(user_prompt)]
    response = model.invoke(messages)
    return _message_to_text(response)


def run_with_tools(
    system_prompt: str,
    user_prompt: str,
    tools: list[BaseTool],
    max_rounds: int = 4,
    temperature: float | None = None,
) -> str:
    """执行一次带工具能力的模型调用。"""
    if not tools:
        return run_completion(system_prompt=system_prompt, user_prompt=user_prompt, temperature=temperature)

    model = get_chat_model(temperature=temperature).bind_tools(tools)
    messages: list[Any] = [SystemMessage(system_prompt), HumanMessage(user_prompt)]
    tool_map = {tool.name: tool for tool in tools}

    for _ in range(max_rounds):
        ai_msg = model.invoke(messages)
        messages.append(ai_msg)

        tool_calls = getattr(ai_msg, "tool_calls", None)
        if not tool_calls:
            return _message_to_text(ai_msg)

        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {}) or {}
            call_id = call.get("id", tool_name)

            tool = tool_map.get(tool_name)
            if tool is None:
                tool_result = f"Tool '{tool_name}' not found."
            else:
                try:
                    tool_result = tool.invoke(tool_args)
                except Exception as exc:
                    tool_result = f"Tool '{tool_name}' execution error: {exc}"

            messages.append(ToolMessage(content=str(tool_result), tool_call_id=call_id))

    final_msg = model.invoke(messages)
    return _message_to_text(final_msg)
