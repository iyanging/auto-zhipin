import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from enum import Enum
from functools import wraps
from typing import Any, ClassVar, Literal

import pendulum
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncSessionTransaction,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column
from sqlalchemy.orm.session import make_transient
from sqlalchemy.types import TypeEngine

from auto_zhipin.settings import settings


class Base(MappedAsDataclass, DeclarativeBase, kw_only=True):
    # Cite from old version docs [https://docs.sqlalchemy.org/en/14/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession]:
    #     The Column.server_default value on the XXX column
    #           will not be refreshed by default after an INSERT;
    #     instead, it is normally expired so that it can be loaded when needed.
    #
    #     Similar behavior applies to a column where the Column.default parameter
    #           is assigned to a SQL expression object.
    #
    #     To access this value with asyncio, it has to be refreshed within the flush process,
    #     which is achieved by setting the mapper.eager_defaults parameter on the mapping
    #
    # Also see:
    #     https://docs.sqlalchemy.org/en/20/orm/persistence_techniques.html#orm-server-defaults
    #     https://docs.sqlalchemy.org/en/20/orm/mapping_api.html#sqlalchemy.orm.Mapper.params.eager_defaults
    __mapper_args__: ClassVar[Any] = {"eager_defaults": True}

    type_annotation_map: ClassVar[dict[Any, TypeEngine[Any]]] = {
        Enum: sa.Enum(
            Enum,
            native_enum=False,
            # By default it uses the length of the longest value.
            # The Enum.length parameter is used ** unconditionally ** for VARCHAR rendering
            # regardless of the Enum.native_enum parameter
            length=255,
        ),
        datetime: sa.DateTime(timezone=True),
    }


class DatabaseContext:
    _logger: ClassVar[logging.Logger] = logging.getLogger(__qualname__)

    session_maker: async_sessionmaker[AsyncSession]
    session_ctx: ContextVar[AsyncSession | None]

    def __init__(self) -> None:
        super().__init__()

        engine = create_async_engine(settings.database_url)
        self.session_maker = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        self.session_ctx = ContextVar("session_ctx", default=None)

    def get(self) -> AsyncSession:
        session = self.session_ctx.get()
        if session is None:
            raise RuntimeError("No active session found in context.")

        return session

    @asynccontextmanager
    async def begin(
        self,
    ) -> AsyncIterator[AsyncSessionTransaction]:
        session = self.session_maker()

        token = self.session_ctx.set(session)

        try:
            yield session.begin()

        except BaseException:
            await session.rollback()
            raise

        finally:
            try:
                await session.commit()

            except BaseException:
                self._logger.exception("Error occurred when commit transaction")

            self.session_ctx.reset(token)

            await session.close()

    def transactional[**P, R](
        self,
    ) -> Callable[
        [Callable[P, Awaitable[R]]],
        Callable[P, Awaitable[R]],
    ]:
        def wrapper(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
            @wraps(func)
            async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
                existing_session = self.session_ctx.get()

                if existing_session is None:
                    async with self.begin():
                        # session will auto commit if everything goes right
                        return await func(*args, **kwargs)

                else:
                    return await func(*args, **kwargs)

            return wrapped

        return wrapper


class TimeMixin(MappedAsDataclass, kw_only=True):
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        default_factory=lambda: pendulum.now(settings.timezone),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=lambda: pendulum.now(settings.timezone),
        default_factory=lambda: pendulum.now(settings.timezone),
    )


class Cookie(Base, TimeMixin):
    __tablename__ = "cookie"

    name: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    domain: Mapped[str | None] = mapped_column(primary_key=True)
    path: Mapped[str | None] = mapped_column(primary_key=True)
    expires: Mapped[Decimal | None]
    http_only: Mapped[bool | None]
    secure: Mapped[bool | None]
    same_site: Mapped[Literal["Lax", "None", "Strict"] | None]
    partition_key: Mapped[str | None]

    logger: ClassVar[logging.Logger] = logging.getLogger(__qualname__)

    @classmethod
    async def overwrite_all(cls, session: AsyncSession, cookies: "Sequence[Cookie]") -> None:
        _ = await session.execute(sa.delete(cls))

        for cookie in cookies:
            make_transient(cookie)

        session.add_all(cookies)
        await session.flush(cookies)

        cls.logger.debug("Refreshed cookies saved: %s", cookies)

    @classmethod
    async def fetch_all(cls, session: AsyncSession) -> "Sequence[Cookie]":
        cookies = (await session.execute(sa.select(cls))).scalars().all()

        cls.logger.debug("Fetched all cookies: %s", cookies)

        return cookies


class JobDetail(Base, TimeMixin):
    __tablename__ = "job_detail"

    company_encrypt_brand_id: Mapped[str] = mapped_column(comment="公司-id")
    company_brand_name: Mapped[str] = mapped_column(comment="公司-名称, eg: 字节跳动")
    company_stage_name: Mapped[str] = mapped_column(comment="公司-融资阶段, eg: 已上市")
    company_scale_name: Mapped[str] = mapped_column(comment="公司-规模, eg: 100-499人")
    company_industry_name: Mapped[str] = mapped_column(comment="公司-行业分类, eg: 互联网")
    company_introduce: Mapped[str] = mapped_column(comment="公司-介绍")

    job_encrypt_id: Mapped[str] = mapped_column(primary_key=True, comment="职位-id")
    job_name: Mapped[str] = mapped_column(comment="职位-名称, eg: 高级python开发工程师")
    job_city_name: Mapped[str] = mapped_column(comment="职位-工作地-城市, eg: 杭州")
    job_area_district: Mapped[str] = mapped_column(comment="职位-工作地-区域, eg: 西湖区")
    job_business_district: Mapped[str] = mapped_column(comment="职位-工作地-商圈, eg: 西溪")
    job_address: Mapped[str] = mapped_column(comment="职位-工作地-详细地址")
    job_experience_name: Mapped[str] = mapped_column(comment="职位-经验要求, eg: 5-10年")
    job_degree: Mapped[str] = mapped_column(comment="职位-学历要求, eg: 本科")
    job_salary_description: Mapped[str] = mapped_column(comment="职位-薪资待遇, eg: 12-24K")
    job_description: Mapped[str] = mapped_column(comment="职位-职位详情")

    @classmethod
    async def save(cls, session: AsyncSession, job: "JobDetail") -> None:
        _ = await session.merge(job)
        await session.flush()


class JobEvaluation(Base, TimeMixin):
    __tablename__ = "job_evaluation"

    job_encrypt_id: Mapped[str] = mapped_column(primary_key=True)

    technology_match_score: Mapped[Decimal] = mapped_column(comment="技术匹配度-0~5分")
    technology_match_reason: Mapped[str] = mapped_column(comment="技术匹配度-打分原因")

    project_experience_match_score: Mapped[Decimal] = mapped_column(comment="项目经验匹配度-0~5分")
    project_experience_match_reason: Mapped[str] = mapped_column(comment="项目经验匹配度-打分原因")

    industry_experience_match_score: Mapped[Decimal] = mapped_column(
        comment="行业经验匹配度-0~5分"
    )
    industry_experience_match_reason: Mapped[str] = mapped_column(
        comment="行业经验匹配度-打分原因"
    )

    level_match_score: Mapped[Decimal] = mapped_column(comment="职级匹配度-0~5分")
    level_match_reason: Mapped[str] = mapped_column(comment="职级匹配度-打分原因")

    growth_potential_score: Mapped[Decimal] = mapped_column(comment="工作/管理潜力-0~5分")
    growth_potential_reason: Mapped[str] = mapped_column(comment="工作/管理潜力-打分原因")

    technical_depth_potential_score: Mapped[Decimal] = mapped_column(comment="技术潜力-0~5分")
    technical_depth_potential_reason: Mapped[str] = mapped_column(comment="技术潜力-打分原因")

    @classmethod
    async def save(cls, session: AsyncSession, evaluation: "JobEvaluation") -> None:
        _ = await session.merge(evaluation)
        await session.flush()
