from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    bot_username: str = Field(alias="BOT_USERNAME")
    admin_user_id: int = Field(alias="ADMIN_USER_ID")
    default_timezone: str = Field(default="Europe/Moscow", alias="DEFAULT_TIMEZONE")
    database_url: str = Field(alias="DATABASE_URL")
    panel_token: str = Field(alias="PANEL_TOKEN")
    public_base_url: str = Field(default="http://localhost", alias="PUBLIC_BASE_URL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")


@lru_cache
def get_settings() -> Settings:
    return Settings()

