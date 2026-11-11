"""Provider-agnostic chat model factory.

Which LLM backs the agent is decided purely by configuration (`LLM_PROVIDER`,
`LLM_MODEL`, ...), so switching providers needs no code changes.
"""

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import Settings


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Build the chat model described by `settings`.

    Raises:
        ValueError: if `settings.llm_provider` is not a supported provider.
    """
    kwargs: dict[str, Any] = {"temperature": settings.llm_temperature}
    if settings.llm_provider == "ollama":
        kwargs["base_url"] = settings.ollama_base_url
        kwargs["num_ctx"] = settings.ollama_num_ctx
    try:
        model: BaseChatModel = init_chat_model(
            model=settings.llm_model, model_provider=settings.llm_provider, **kwargs
        )
        return model
    except ValueError as exc:
        raise ValueError(f"Unsupported LLM provider {settings.llm_provider!r}: {exc}") from exc
