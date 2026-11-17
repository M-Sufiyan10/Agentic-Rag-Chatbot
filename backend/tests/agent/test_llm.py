import pytest
from langchain_ollama import ChatOllama

from app.agent.llm import build_chat_model
from app.config import Settings


def test_build_chat_model_ollama() -> None:
    settings = Settings(llm_provider="ollama", llm_model="m1", ollama_base_url="http://h:1")
    model = build_chat_model(settings)
    assert isinstance(model, ChatOllama)
    assert model.model == "m1"
    assert model.base_url == "http://h:1"
    assert model.num_ctx == 8192


def test_build_chat_model_applies_num_ctx() -> None:
    model = build_chat_model(Settings(llm_provider="ollama", ollama_num_ctx=16384))
    assert isinstance(model, ChatOllama)
    assert model.num_ctx == 16384


def test_history_budget_must_fit_ollama_context() -> None:
    with pytest.raises(ValueError, match="OLLAMA_NUM_CTX"):
        Settings(llm_provider="ollama", ollama_num_ctx=4096, llm_max_history_tokens=6000)
    Settings(llm_provider="ollama", ollama_num_ctx=7500, llm_max_history_tokens=6000)
    Settings(llm_provider="openai", ollama_num_ctx=4096, llm_max_history_tokens=6000)


def test_build_chat_model_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="nope"):
        build_chat_model(Settings(llm_provider="nope"))


def test_build_chat_model_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = build_chat_model(
        Settings(llm_provider="anthropic", llm_model="claude-haiku-5-5", llm_temperature=0.2)
    )
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-haiku-5-5"
    # Current Claude models reject sampling parameters, so none is sent.
    assert model.temperature is None
    assert model.max_tokens == 16000


def test_hosted_max_tokens_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    model = build_chat_model(
        Settings(llm_provider="anthropic", llm_model="claude-haiku-5-5", llm_max_tokens=4000)
    )
    assert isinstance(model, ChatAnthropic)
    assert model.max_tokens == 4000


def test_api_key_from_settings_reaches_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    model = build_chat_model(
        Settings(
            llm_provider="anthropic", llm_model="claude-haiku-5-5", llm_api_key="key-from-settings"
        )
    )
    assert isinstance(model, ChatAnthropic)
    assert model.anthropic_api_key.get_secret_value() == "key-from-settings"


def test_ollama_keeps_its_temperature() -> None:
    model = build_chat_model(Settings(llm_provider="ollama", llm_temperature=0.3))
    assert isinstance(model, ChatOllama)
    assert model.temperature == 0.3
