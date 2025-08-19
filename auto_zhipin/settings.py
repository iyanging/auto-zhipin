from pydantic import SecretStr
from pydantic_settings import BaseSettings

__all__ = ["settings"]


class Settings(BaseSettings):
    timezone: str = "Asia/Shanghai"

    llm_model: str
    llm_api_key: SecretStr

    database_url: str = "sqlite+aiosqlite:///auto_zhipin.db"


settings = Settings()  # pyright: ignore[reportCallIssue]
