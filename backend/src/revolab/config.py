from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REVOLAB_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./revolab.db"
    app_name: str = "REvoLab"
    environment: str = "development"
    # Local ContentStore root (fsspec local backend). Immutable byte artifacts.
    content_root: str = ".revolab-content"
    # Comma-separated CORS origins (configurable, not a hardcoded asset host).
    cors_origins: str = "http://localhost:5173"
    # Phase-4 REvoCompute driver configuration. A driver is installed only when
    # `revocompute_base_url` is set; otherwise the Provider Catalog remains the
    # honest empty set.
    revocompute_base_url: str | None = None
    revocompute_timeout_seconds: float = 30.0
    # Opt-in in-process fake COMPUTE provider for the browser/e2e vertical slice.
    # Default OFF in production; never enables any real external credential flow.
    e2e_fake_compute: bool = False
    # Explicit runtime root for the canonical skill tree. Defaults to the repo
    # `.agents/skills/` during development; a packaged deployment MUST set this
    # to a shipped skill root (the repo-relative path is not part of the wheel).
    # `get_settings()` is lru_cached, so set REVOLAB_SKILLS_ROOT before the first
    # settings access (process start) or call `get_settings.cache_clear()`.
    skills_root: str | None = None

    # --- Phase-8 Bounded Project Agent Runtime (server-owned configuration) ---
    # One OpenAI-compatible chat/tool-call transport. `model_endpoint` is the
    # base URL (or the full `/chat/completions` URL); `model_name` the concrete
    # model id. A missing configuration fails explicitly — never a silent fake.
    model_endpoint: str | None = None
    model_name: str | None = None
    # Optional bearer credential. SecretStr never leaks through repr/log/OpenAPI;
    # it is presented by the transport only, never echoed anywhere else.
    model_api_key: SecretStr | None = None
    model_timeout_seconds: float = 60.0
    # Opt-in in-process scripted fake model for tests/browser slice (never in
    # production; only used when no real model endpoint is configured).
    e2e_fake_model: bool = False
    # Conservative Agent-loop ceilings (TODO.md section 7).
    agent_max_model_turns: int = 8
    agent_max_tool_calls: int = 16
    agent_max_tool_calls_per_turn: int = 4
    agent_max_context_chars: int = 60_000
    agent_max_history_messages: int = 20
    agent_max_history_chars: int = 20_000
    agent_max_skill_count: int = 4
    agent_max_skill_bytes: int = 20_000
    agent_max_tool_result_chars: int = 12_000
    agent_total_turn_duration_seconds: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
