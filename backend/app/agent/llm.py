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
    kwargs: dict[str, Any]
    if settings.llm_provider == "ollama":
        kwargs = {
            "temperature": settings.llm_temperature,
            "base_url": settings.ollama_base_url,
            "num_ctx": settings.ollama_num_ctx,
        }
    else:
        # Hosted models: current Claude models reject sampling parameters such as
        # ``temperature`` (HTTP 400), so none is sent. ``max_tokens`` caps each reply;
        # without it the Anthropic integration asks for the model's maximum, which is
        # too large for a non-streaming request.
        kwargs = {"max_tokens": settings.llm_max_tokens}
        api_key = settings.llm_api_key
        if api_key is not None and api_key.get_secret_value():
            kwargs["api_key"] = api_key.get_secret_value()
    try:
        model: BaseChatModel = init_chat_model(
            model=settings.llm_model, model_provider=settings.llm_provider, **kwargs
        )
        return model
    except ValueError as exc:
        raise ValueError(f"Unsupported LLM provider {settings.llm_provider!r}: {exc}") from exc
