from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_username: str = Field(alias="BOT_USERNAME")
    admin_user_id: int = Field(alias="ADMIN_USER_ID")
    default_timezone: str = Field(default="Europe/Moscow", alias="DEFAULT_TIMEZONE")
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    postgres_db: str = Field(default="muadreminder", alias="POSTGRES_DB")
    postgres_user: str = Field(default="muad", alias="POSTGRES_USER")
    postgres_password: str = Field(default="muad", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="db", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    panel_token: str = Field(alias="PANEL_TOKEN")
    public_base_url: str = Field(default="http://localhost", alias="PUBLIC_BASE_URL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
