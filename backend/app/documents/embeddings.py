"""Provider-agnostic embedding model construction."""

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings

from app.config import Settings


class PrefixedEmbeddings(Embeddings):
    """Prepend task prefixes before embedding, e.g. nomic's ``search_query: ``.

    Retrieval-tuned models such as nomic-embed-text are trained with one prefix
    for questions and another for passages; without them, questions about a
    document score barely closer to it than unrelated questions do.
    """

    def __init__(self, inner: Embeddings, *, query_prefix: str, document_prefix: str) -> None:
        self.inner = inner
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([self.document_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self.query_prefix + text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.inner.aembed_documents([self.document_prefix + t for t in texts])

    async def aembed_query(self, text: str) -> list[float]:
        return await self.inner.aembed_query(self.query_prefix + text)


def build_embeddings(settings: Settings) -> Embeddings:
    """Build the configured embedding model; switching provider is config-only."""
    provider = settings.embedding_provider
    kwargs = {"base_url": settings.ollama_base_url} if provider == "ollama" else {}
    try:
        inner = init_embeddings(f"{provider}:{settings.embedding_model}", **kwargs)
    except ValueError as exc:
        raise ValueError(f"Unsupported embedding provider {provider!r}: {exc}") from exc
    query_prefix = settings.embedding_query_prefix or ""
    document_prefix = settings.embedding_document_prefix or ""
    if not query_prefix and not document_prefix:
        return inner
    return PrefixedEmbeddings(inner, query_prefix=query_prefix, document_prefix=document_prefix)
