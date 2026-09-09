from functools import lru_cache

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
    skills_root: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
