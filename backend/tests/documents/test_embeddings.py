"""Embedding task prefixes and the tag stored with each chunk."""

from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.documents.embeddings import PrefixedEmbeddings, build_embeddings


class Recorder(Embeddings):
    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.seen.append(text)
        return [0.0]


def test_nomic_gets_its_task_prefixes_by_default() -> None:
    settings = Settings(embedding_model="nomic-embed-text")
    assert settings.embedding_query_prefix == "search_query: "
    assert settings.embedding_document_prefix == "search_document: "
    assert settings.embedding_tag == (
        "nomic-embed-text (query='search_query: ', document='search_document: ')"
    )


def test_other_models_get_no_prefixes_and_a_plain_tag() -> None:
    settings = Settings(embedding_model="mxbai-embed-large")
    assert settings.embedding_query_prefix == ""
    assert settings.embedding_document_prefix == ""
    assert settings.embedding_tag == "mxbai-embed-large"


def test_prefixes_can_be_turned_off() -> None:
    settings = Settings(
        embedding_model="nomic-embed-text", embedding_query_prefix="", embedding_document_prefix=""
    )
    assert settings.embedding_tag == "nomic-embed-text"


async def test_prefixed_embeddings_prefix_queries_and_documents() -> None:
    inner = Recorder()
    prefixed = PrefixedEmbeddings(inner, query_prefix="q: ", document_prefix="d: ")

    prefixed.embed_query("a")
    prefixed.embed_documents(["b", "c"])
    await prefixed.aembed_query("e")
    await prefixed.aembed_documents(["f"])

    assert inner.seen == ["q: a", "d: b", "d: c", "q: e", "d: f"]


def test_build_embeddings_wraps_only_when_prefixes_are_set() -> None:
    assert isinstance(
        build_embeddings(Settings(embedding_model="nomic-embed-text")), PrefixedEmbeddings
    )
    assert not isinstance(
        build_embeddings(Settings(embedding_model="mxbai-embed-large")), PrefixedEmbeddings
    )
