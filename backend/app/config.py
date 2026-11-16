"""Application settings, loaded from environment variables and an optional .env file."""

from functools import lru_cache

from ddgs.engines import ENGINES
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONTEXT_HEADROOM_TOKENS = 1500


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot"
    jwt_secret: str = "change-me"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    cookie_secure: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    llm_temperature: float = 0.2
    llm_max_history_tokens: int = 6000
    agent_max_tool_rounds: int = 5
    ollama_base_url: str = "http://localhost:11434"
    ollama_num_ctx: int = 8192
    web_search_max_results: int = 5
    web_search_timeout_seconds: int = 10
    web_search_backend: str = "duckduckgo,brave,yahoo,startpage"
    embedding_provider: str = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    # Task prefixes some embedding models are trained with. ``None`` means "the model's
    # default": nomic-embed-text expects "search_query: " / "search_document: ".
    embedding_query_prefix: str | None = None
    embedding_document_prefix: str | None = None
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 10
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_k: int = 4
    # Search the user's documents before every answer, keeping passages at most this
    # cosine distance from the question (0 = identical, 1 = unrelated).
    auto_document_search: bool = True
    auto_document_max_distance: float = 0.48
    # Search the web before answering when the message looks like it needs fresh facts.
    auto_web_search: bool = True
    knowledge_cutoff_year: int = 2024
    max_document_chunks: int = 500
    mcp_config_path: str = "mcp_servers.json"
    mcp_tool_timeout_seconds: float = 30
    mcp_startup_timeout_seconds: float = 20
    # Optional Langfuse tracing: on only when both keys are set.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "http://localhost:3100"
    langfuse_timeout_seconds: float = 5

    @model_validator(mode="after")
    def _default_embedding_prefixes(self) -> "Settings":
        nomic = self.embedding_model.startswith("nomic-embed-text")
        if self.embedding_query_prefix is None:
            self.embedding_query_prefix = "search_query: " if nomic else ""
        if self.embedding_document_prefix is None:
            self.embedding_document_prefix = "search_document: " if nomic else ""
        return self

    @property
    def embedding_tag(self) -> str:
        """What stored chunks record as their ``embedding_model``.

        Vectors are only comparable when the model *and* the prefixes match, so
        the prefixes are part of the tag; chunks embedded otherwise count as
        mismatched until the reindex command re-embeds them.
        """
        query, document = self.embedding_query_prefix or "", self.embedding_document_prefix or ""
        if not query and not document:
            return self.embedding_model
        return f"{self.embedding_model} (query={query!r}, document={document!r})"

    @field_validator("web_search_backend")
    @classmethod
    def _web_search_backend_is_known(cls, value: str) -> str:
        available = sorted(ENGINES["text"])
        unknown = [n.strip() for n in value.split(",") if n.strip() not in {"auto", *available}]
        if unknown or not value.strip():
            raise ValueError(
                f"WEB_SEARCH_BACKEND has unknown engine(s) {unknown or [value]}; "
                f"use 'auto' or a comma list of: {', '.join(available)}"
            )
        return value

    @model_validator(mode="after")
    def _history_budget_fits_context(self) -> "Settings":
        limit = self.ollama_num_ctx - _CONTEXT_HEADROOM_TOKENS
        if self.llm_provider == "ollama" and self.llm_max_history_tokens > limit:
            raise ValueError(
                f"LLM_MAX_HISTORY_TOKENS ({self.llm_max_history_tokens}) must be at most "
                f"OLLAMA_NUM_CTX - {_CONTEXT_HEADROOM_TOKENS} ({limit}) so the history, system "
                "prompt, tool schemas and reply fit in the model's context window"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
