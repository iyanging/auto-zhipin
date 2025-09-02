import asyncio
import logging.config
from pathlib import Path
from typing import Annotated

import colorlog
import sqlalchemy as sa
import typer
from alembic.config import CommandLine as AlembicCommandLine
from asyncer import runnify

from auto_zhipin.boss_zhipin import BossZhipin
from auto_zhipin.db import Cookie, DatabaseContext, JobDetail, JobEvaluation
from auto_zhipin.evaluator import evaluate_job

logger = logging.getLogger(__name__)

db = DatabaseContext()


class Logic:
    @staticmethod
    async def seek(*, from_url: str, job_count: int) -> None:
        async with BossZhipin(headless=False) as boss_zhipin:
            async with db.begin():
                saved_cookies = await Cookie.fetch_all(db.get())

            refreshed_cookies = await boss_zhipin.login(saved_cookies)

            async with db.begin():
                await Cookie.overwrite_all(db.get(), refreshed_cookies)

            async for job in boss_zhipin.seek_jobs(from_url, job_count):
                async with db.begin():
                    await JobDetail.save(db.get(), job)

                logger.info("Saved %s", job)

    @staticmethod
    async def evaluate(*, resume_path: Path, job_count: int, concurrency: int) -> None:
        resume = resume_path.read_text(encoding="utf-8")

        async with db.begin():
            unevaluated_job_list = (
                (
                    await db.get().execute(
                        sa.select(JobDetail)
                        .join(
                            JobEvaluation,
                            JobDetail.job_encrypt_id == JobEvaluation.job_encrypt_id,
                            isouter=True,
                        )
                        .where(JobEvaluation.job_encrypt_id.is_(None))
                        .order_by(JobDetail.created_at.asc())
                        .limit(job_count)
                    )
                )
                .scalars()
                .all()
            )

        job_queue = asyncio.Queue[JobDetail](concurrency)

        workers = [
            asyncio.create_task(
                Logic._evaluator(
                    resume,
                    job_queue,
                )
            )
            for _ in range(concurrency)
        ]

        for job in unevaluated_job_list:
            await job_queue.put(job)

        # 已经查询完所有的job，等待worker空闲
        await job_queue.join()

        # 销毁所有worker
        for w in workers:
            _ = w.cancel()

        _ = await asyncio.gather(*workers, return_exceptions=True)

    @staticmethod
    async def _evaluator(resume: str, job_queue: asyncio.Queue[JobDetail]) -> None:
        while True:
            job = await job_queue.get()

            try:
                evaluation = await evaluate_job(resume, job)

                async with db.begin():
                    await JobEvaluation.save(db.get(), evaluation)

            finally:
                job_queue.task_done()


app = typer.Typer()


@app.command()
@runnify
async def seek(
    *,
    from_url: Annotated[str, typer.Option(help="The filtered job list URL")],
    job_count: Annotated[int, typer.Option(help="The job count of current evaluation")],
) -> None:
    await Logic().seek(
        from_url=from_url,
        job_count=job_count,
    )


@app.command()
@runnify
async def evaluate(
    *,
    resume_path: Annotated[Path, typer.Option(help="The path of resume file (text)")],
    job_count: Annotated[int, typer.Option(help="The job count of this evaluation")],
    concurrency: Annotated[int, typer.Option(help="The max concurrency of this evaluation")] = 7,
) -> None:
    await Logic().evaluate(
        resume_path=resume_path,
        job_count=job_count,
        concurrency=concurrency,
    )


@app.callback()
def describe() -> None:
    setup_logging()

    alembic_upgrade_head()


def alembic_upgrade_head():
    AlembicCommandLine("alembic").main(["upgrade", "head"])


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
