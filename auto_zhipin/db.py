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


class Base(MappedAsDataclass, DeclarativeBase):
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
                    token = None
                    try:
                        async with self.begin() as tx:
                            token = self.session_ctx.set(tx.session)

                            # session will auto commit if everything goes right
                            return await func(*args, **kwargs)

                    except BaseException:
                        if token is not None:
                            self.session_ctx.reset(token)

                        raise

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


class Cookie(Base):
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


class JobEvaluation(Base):
    __tablename__ = "job_evaluation"

    job_encrypt_id: Mapped[str] = mapped_column(primary_key=True)
