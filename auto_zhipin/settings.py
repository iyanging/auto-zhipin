from pydantic import SecretStr
from pydantic_settings import BaseSettings

from auto_zhipin.llm import LLMModel

__all__ = ["settings"]


class Settings(BaseSettings):
    timezone: str = "Asia/Shanghai"

    llm_model: LLMModel
    llm_base_url: str | None = None
    llm_api_key: SecretStr

    database_url: str = "sqlite+aiosqlite:///auto_zhipin.db"


settings = Settings()  # pyright: ignore[reportCallIssue]
