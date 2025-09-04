import asyncio
import logging.config
from pathlib import Path
from random import randint
from typing import Annotated, Any

import colorlog
import sqlalchemy as sa
import typer
import uvicorn
from alembic.config import CommandLine as AlembicCommandLine
from asyncer import runnify
from pydantic_ai.models import Model

from auto_zhipin.boss_zhipin import BossZhipin
from auto_zhipin.dashboard import app as dashboard_app
from auto_zhipin.db import Cookie, JobDetail, JobEvaluation
from auto_zhipin.deps import db
from auto_zhipin.evaluator import evaluate_job
from auto_zhipin.llm import LLMModel, build_model

logger = logging.getLogger(__name__)


class Logic:
    @staticmethod
    async def seek(*, from_url: str, job_count: int, debug: bool, headless: bool) -> None:
        async with BossZhipin(debug=debug, headless=headless) as boss_zhipin:
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
    async def evaluate(
        *,
        resume_path: Path,
        job_count: int,
        concurrency: int,
        llm_model: LLMModel,
        llm_base_url: str | None,
        llm_api_key: str,
    ) -> None:
        resume = resume_path.read_text(encoding="utf-8")

        model = build_model(
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
        )

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
                    model=model,
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
    async def _evaluator(
        resume: str,
        job_queue: asyncio.Queue[JobDetail],
        model: Model,
    ) -> None:
        while True:
            job = await job_queue.get()

            try:
                evaluation = await evaluate_job(
                    resume=resume,
                    job=job,
                    model=model,
                )

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
    debug: Annotated[bool, typer.Option(help="Whether to turn on debug")] = False,
    headless: Annotated[bool, typer.Option(help="Whether to run browser in headless mode")] = True,
) -> None:
    await Logic().seek(
        from_url=from_url,
        job_count=job_count,
        debug=debug,
        headless=headless,
    )


@app.command()
@runnify
async def evaluate(
    *,
    resume_path: Annotated[Path, typer.Option(help="The path of resume file (text)")],
    job_count: Annotated[int, typer.Option(help="The job count of this evaluation")],
    concurrency: Annotated[int, typer.Option(help="The max concurrency of this evaluation")] = 7,
    llm_model: Annotated[LLMModel, typer.Option(help="The LLM model which is running evaluation")],
    llm_base_url: Annotated[str | None, typer.Option(help="The LLM model service url")] = None,
    llm_api_key: Annotated[
        str,
        typer.Option(
            help="The LLM model service api-key",
            envvar="AUTO_ZHIPIN_LLM_API_KEY",
        ),
    ],
) -> None:
    await Logic().evaluate(
        resume_path=resume_path,
        job_count=job_count,
        concurrency=concurrency,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
    )


@app.command()
def review(
    *,
    host: Annotated[str, typer.Option(help="The host of dashboard")] = "localhost",
    port: Annotated[int | None, typer.Option(help="The port of dashboard")] = None,
):
    if port is None:
        port = randint(1000, 65534)  # noqa: S311

    dashboard_app.router.add_event_handler(
        "startup",
        lambda: typer.launch(f"http://{host}:{port}"),
    )

    uvicorn.run(
        dashboard_app,
        host=host,
        port=port,
        log_config=get_logging_config(),
    )


@app.callback()
def describe() -> None:
    setup_logging()

    alembic_upgrade_head()


def alembic_upgrade_head():
    AlembicCommandLine("alembic").main(["upgrade", "head"])


def get_logging_config() -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "app": {
                "class": (
                    f"{colorlog.ColoredFormatter.__module__}.{colorlog.ColoredFormatter.__name__}"
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
        "loggers": {
            "httpx": {
                "level": logging.getLevelName(logging.WARNING),
            },
        },
    }


def setup_logging():
    logging.config.dictConfig(get_logging_config())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
