from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REVOLAB_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./revolab.db"
    app_name: str = "REvoLab"
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
