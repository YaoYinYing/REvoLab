from functools import lru_cache

from pydantic import Field, SecretStr
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
    # --- Phase-13 NCBI PubMed literature-discovery provider (server-owned) ---
    # NCBI E-utilities require an application `tool` identifier and an operator
    # contact `email` on every request (current official NCBI usage policy). They
    # are OPERATOR configuration, never Project data, and are never hard-coded.
    # The driver is installed only when BOTH are configured; a partially
    # configured deployment fails loudly instead of silently probing anonymously.
    ncbi_tool: str | None = None
    ncbi_email: str | None = None
    ncbi_timeout_seconds: float = 10.0
    # Conservative no-key pacing: NCBI documents a maximum of 3 E-utilities
    # requests per second per IP without an API key. Phase 13 has no API-key
    # feature (an explicit non-goal), so the default interval stays below that
    # ceiling (~2.9 req/s). Tests inject 0; production must not lower it.
    ncbi_min_request_interval_seconds: float = 0.34
    # Opt-in in-process fake LITERATURE provider for application/browser slices.
    # Default OFF in production; it realizes the SAME capability boundary through
    # the SAME Driver/Capability registry as the real driver.
    e2e_fake_literature: bool = False
    # --- Phase-14 UniProt protein-discovery provider (server-owned) ---
    # The public UniProt REST surface is unauthenticated and the current official
    # documentation publishes NO numeric rate limit and NO required User-Agent or
    # contact convention, so there is deliberately no invented pacing constant and
    # no credential; the driver makes exactly ONE bounded request per search/resolve
    # and only the bounded transport timeout is configurable.
    #
    # Like the NCBI literature provider, the real remote driver is installed only
    # when the deployment explicitly enables it: a zero-config deployment keeps the
    # Provider Catalog the honest empty set, and CI/browser slices never perform a
    # live UniProt request (they use the in-process fake below).
    uniprot_discovery_enabled: bool = False
    uniprot_timeout_seconds: float = 15.0
    # Opt-in in-process fake PROTEIN provider for application/browser slices.
    # Default OFF in production; it realizes the SAME capability boundary through
    # the SAME Driver/Capability registry as the real driver, and claims its OWN
    # `fakeuniprot` authority so a mixed registration fails the collision check.
    e2e_fake_protein: bool = False
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
    agent_max_history_messages: int = Field(default=20, ge=0)
    agent_max_history_chars: int = Field(default=20_000, ge=0)
    agent_max_skill_count: int = 4
    agent_max_skill_bytes: int = 20_000
    agent_max_tool_result_chars: int = 12_000
    agent_total_turn_duration_seconds: float = 300.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
