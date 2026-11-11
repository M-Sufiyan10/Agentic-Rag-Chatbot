"""Provider-agnostic embedding model construction."""

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from app.config import Settings


def build_embeddings(settings: Settings) -> Embeddings:
    """Build the configured embedding model; switching provider is config-only."""
    provider = settings.embedding_provider
    kwargs = {"base_url": settings.ollama_base_url} if provider == "ollama" else {}
    try:
        return init_embeddings(f"{provider}:{settings.embedding_model}", **kwargs)
    except ValueError as exc:
        raise ValueError(f"Unsupported embedding provider {provider!r}: {exc}") from exc
