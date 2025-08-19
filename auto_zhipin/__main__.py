import asyncio
import logging.config
from typing import Annotated

import colorlog
import typer

from auto_zhipin.boss_zhipin import BossZhipin
from auto_zhipin.db import Cookie, DatabaseContext


async def amain(*, from_url: str) -> None:
    db = DatabaseContext()

    @db.transactional()
    async def auto_zhipin() -> None:
        async with BossZhipin() as boss_zhipin:

            saved_cookies = await Cookie.fetch_all(db.get())

            refreshed_cookies = await boss_zhipin.login(saved_cookies)

            await Cookie.overwrite_all(db.get(), refreshed_cookies)

            boss_zhipin.

    await auto_zhipin()


def main(
    *,
    from_url: Annotated[str, typer.Argument(help="The filtered job list URL")],
):
    setup_logging()

    asyncio.run(
        amain(
            from_url=from_url,
        )
    )


def setup_logging():
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "app": {
                    "class": (
                        f"{colorlog.ColoredFormatter.__module__}."
                        f"{colorlog.ColoredFormatter.__name__}"
                    ),
                    "format": (
                        "%(process)-5d %(taskName)-8s %(asctime)s "
                        "%(log_color)s%(levelname)-8s%(reset)s "
                        "%(name)-24s %(log_color)s%(message)s%(reset)s"
                    ),
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "app",
                }
            },
            "root": {
                "level": logging.getLevelName(logging.INFO),
                "handlers": ["console"],
            },
        }
    )


if __name__ == "__main__":
    typer.run(main)
