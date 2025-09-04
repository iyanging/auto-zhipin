from pathlib import Path

__all__ = ["APP_ROOT", "settings"]


class Settings:
    timezone: str = "Asia/Shanghai"
    database_url: str = "sqlite+aiosqlite:///auto_zhipin.db"


settings = Settings()

APP_ROOT = Path(__file__).parent
