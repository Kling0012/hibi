from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    memory_host: str = "127.0.0.1"
    memory_port: int = 8377
    memory_default_user: str = "default"
    memory_default_project: str = "all"
    memory_default_agent: str = "codex"
    memory_session_ttl_seconds: int = 86_400
    memory_admin_token: str | None = None
    mcp_allowed_hosts: str = ""

    valkey_url: str = "redis://localhost:6379/0"
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_database: str = "agent_memory"

    memory_llm_provider: str = "openai_compatible"
    memory_llm_base_url: str = "https://provider.example/v1"
    memory_embedding_base_url: str = "http://localhost:8081/v1"
    memory_embedding_api_key: str = "local-embedding"
    memory_embedding_mode: Literal["local", "cloudflare", "cloudflare_primary_local_fallback"] = "local"
    memory_embedding_cloudflare_model: str = "@cf/qwen/qwen3-embedding-0.6b"
    memory_embedding_cloudflare_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    memory_embedding_fallback_cooldown_seconds: float = 300.0
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    memory_llm_model: str = "glm-5.3-flash"
    memory_llm_small_model: str = "glm-5.3-flash"
    memory_llm_max_tokens: int = 2048
    memory_llm_structured_output_mode: Literal["json_schema", "json_object"] = "json_schema"
    memory_ingestion_mode: Literal["graphiti", "graphiti_async", "typesafe_async"] = "graphiti"
    memory_node_resolution_provider: Literal["llm", "jev"] = "llm"
    memory_node_partial_fallback_mode: Literal["off", "shadow", "on"] = "off"
    memory_decision_backend: Literal["typesafe", "adapter"] = "typesafe"
    memory_decision_adapter_base_url: str | None = None
    memory_decision_adapter_api_key: str | None = None
    memory_decision_adapter_model: str | None = None
    memory_jev_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    memory_jev_batch_size: int = Field(default=40, ge=1, le=120)
    memory_jev_max_concurrency: int = Field(default=3, ge=1, le=3)
    memory_jev_cache_max_entries: int = Field(default=256, ge=0, le=4096)
    memory_jev_cache_ttl_seconds: float = Field(default=300.0, ge=0, le=3600)
    memory_jev_max_pairs: int = Field(default=120, ge=1, le=1000)
    memory_jev_max_context_chars: int = Field(default=24000, ge=1, le=96000)
    memory_jev_max_request_bytes: int = Field(default=96000, ge=1, le=500000)
    memory_jev_max_estimated_tokens: int = Field(default=30000, ge=512, le=30000)
    memory_jev_max_batches: int = Field(default=6, ge=1, le=12)
    memory_jev_max_total_request_bytes: int = Field(default=160000, ge=1, le=500000)
    typesafe_api_key: str | None = None
    memory_typesafe_model: str = "jev-1.13.0"
    memory_decision_confidence: float = Field(default=0.90, gt=0.5, le=1)
    memory_search_rerank_mode: Literal["off", "shadow", "on"] = "off"
    memory_search_rerank_candidates: int = Field(default=15, ge=2, le=50)
    memory_search_rerank_batch_size: int = Field(default=20, ge=1, le=50)
    memory_search_rerank_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    memory_enrichment_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    memory_enrichment_lease_seconds: int = Field(default=240, ge=30, le=3600)
    memory_enrichment_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)
    memory_enrichment_max_attempts: int = Field(default=3, ge=1, le=10)
    memory_embedding_model: str = "qwen3-embedding-0.6b"
    memory_embedding_dim: int = 1024
    memory_llm_api_key: str | None = None
    openai_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
