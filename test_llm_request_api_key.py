from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import llm as llm_service


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _fake_settings(api_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        llm_api_key=api_key,
        llm_model="test-model",
        llm_temperature=0.2,
        llm_max_tokens=123,
        llm_base_url="https://example.test/v1",
    )


def test_get_chat_model_uses_request_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_service, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(llm_service, "get_settings", lambda: _fake_settings("env-key"))

    token = llm_service.set_request_api_key("page-key")
    try:
        model = llm_service.get_chat_model()
    finally:
        llm_service.reset_request_api_key(token)

    assert model.kwargs["api_key"] == "page-key"
    assert model.kwargs["model"] == "test-model"
    assert model.kwargs["base_url"] == "https://example.test/v1"
    assert model.kwargs["max_tokens"] == 123


def test_get_chat_model_uses_explicit_api_key_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_service, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(llm_service, "get_settings", lambda: _fake_settings("env-key"))

    token = llm_service.set_request_api_key("page-key")
    try:
        model = llm_service.get_chat_model(api_key="explicit-key")
    finally:
        llm_service.reset_request_api_key(token)

    assert model.kwargs["api_key"] == "explicit-key"


def test_get_chat_model_raises_without_any_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_service, "get_settings", lambda: _fake_settings(""))

    with pytest.raises(RuntimeError, match="X-LLM-API-Key"):
        llm_service.get_chat_model()
