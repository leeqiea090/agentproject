from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import get_settings


def _message_to_text(message: Any) -> str:
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


def get_chat_model(temperature: float | None = None) -> ChatOpenAI:
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set.")

    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "api_key": settings.llm_api_key,
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
