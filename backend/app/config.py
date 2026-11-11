"""Application settings, loaded from environment variables and an optional .env file."""

from functools import lru_cache

from ddgs.engines import ENGINES
from pydantic import field_validator, model_validator
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
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 10
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_k: int = 4

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
