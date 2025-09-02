__all__ = ["settings"]


class Settings:
    timezone: str = "Asia/Shanghai"
    database_url: str = "sqlite+aiosqlite:///auto_zhipin.db"


settings = Settings()
