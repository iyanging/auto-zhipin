import asyncio
import logging.config
from typing import Annotated

import colorlog
import typer
from alembic.config import CommandLine as AlembicCommandLine

from auto_zhipin.boss_zhipin import BossZhipin, Job
from auto_zhipin.db import Cookie, DatabaseContext
from auto_zhipin.evaluator import evaluate_job


async def amain(*, from_url: str, job_count: int, concurrency: int) -> None:
    db = DatabaseContext()

    async def auto_zhipin() -> None:
        async with BossZhipin(headless=False) as boss_zhipin:
            await login(boss_zhipin)

            job_queue = asyncio.Queue[Job](concurrency)

            workers = [asyncio.create_task(worker(job_queue)) for _ in range(concurrency)]

            async for job in boss_zhipin.query_jobs(from_url, job_count):
                await job_queue.put(job)

            # 已经查询完所有的job，等待worker空闲
            await job_queue.join()

            # 销毁所有worker
            for w in workers:
                _ = w.cancel()

            _ = await asyncio.gather(*workers, return_exceptions=True)

    @db.transactional()
    async def evaluator(job: Job) -> None:
        evaluation = await evaluate_job(job)
        # TODO: save

    async def worker(job_queue: asyncio.Queue[Job]) -> None:
        while True:
            job = await job_queue.get()

            try:
                await evaluator(job)

            finally:
                job_queue.task_done()

    @db.transactional()
    async def login(boss_zhipin: BossZhipin) -> None:
        saved_cookies = await Cookie.fetch_all(db.get())

        refreshed_cookies = await boss_zhipin.login(saved_cookies)

        await Cookie.overwrite_all(db.get(), refreshed_cookies)

    await auto_zhipin()


def alembic_upgrade_head():
    AlembicCommandLine("alembic").main(["upgrade", "head"])


def main(
    *,
    from_url: Annotated[str, typer.Option(help="The filtered job list URL")],
    job_count: Annotated[int, typer.Option(help="The job count of current evaluation")],
    concurrency: Annotated[int, typer.Option(help="The max concurrency of job evaluation")] = 7,
):
    setup_logging()

    alembic_upgrade_head()

    asyncio.run(
        amain(
            from_url=from_url,
            job_count=job_count,
            concurrency=concurrency,
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
