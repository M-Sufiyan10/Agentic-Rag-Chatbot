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


def test_build_chat_model_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="nope"):
        build_chat_model(Settings(llm_provider="nope"))
