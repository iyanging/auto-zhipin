from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    Self,
    cast,
    override,
)

import sqlalchemy as sa
from fastapi import Query
from nicegui import ui
from nicegui.elements.mixins.value_element import ValueElement
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from auto_zhipin.db import JobDetail


class Input[E: ValueElement, T](ABC):
    @abstractmethod
    def build(self) -> E: ...

    @abstractmethod
    @classmethod
    def deserialize(cls, raw: Any) -> T: ...

    @abstractmethod
    @classmethod
    def serialize(cls, value: T) -> Any: ...


@dataclass(kw_only=True)
class TextInput(Input[ui.input, str]):
    label: str | None = None
    placeholder: str | None = None

    @override
    def build(self) -> ui.input:
        return ui.input(
            label=self.label,
            placeholder=self.placeholder,
        )

    @override
    @classmethod
    def deserialize(cls, raw: Any) -> str:
        return str(raw)

    @override
    @classmethod
    def serialize(cls, value: str) -> Any:
        return value


type _DateRangeDict = dict[Literal["from"] | Literal["to"], str]


class _DateRangeStr(RootModel[str]):
    __separator__: ClassVar[str] = " - "

    _from: date
    _to: date

    @model_validator(mode="after")
    def _init(self) -> Self:
        (
            self._from,
            self._to,
        ) = (date.fromisoformat(d.strip()) for d in self.root.split(self.__separator__))

        return self

    @property
    def from_(self) -> date:
        return self._from

    @property
    def to(self) -> date:
        return self._to

    def dict_dump(self) -> _DateRangeDict:
        return {
            "from": self.from_.isoformat(),
            "to": self.to.isoformat(),
        }

    if TYPE_CHECKING:

        @override
        def model_dump(self, **_: Any) -> str: ...


class _DateRangeStruct(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_: Annotated[date, Field(alias="from")]
    to: date

    @model_serializer(mode="plain")
    def __serialize__(self) -> str:
        return f"{self.from_}{_DateRangeStr.__separator__}{self.to}"

    def dict_dump(self) -> _DateRangeDict:
        return {
            "from": self.from_.isoformat(),
            "to": self.to.isoformat(),
        }

    if TYPE_CHECKING:

        @override
        def model_dump(self, **_: Any) -> str: ...  # pyright: ignore[reportIncompatibleMethodOverride]


type DateRange = _DateRangeStruct | _DateRangeStr


@dataclass(kw_only=True)
class DateRangeInput(Input[ui.date, DateRange]):
    __type_adapter__: ClassVar[TypeAdapter[DateRange]] = TypeAdapter[DateRange](DateRange)

    label: str | None = None

    @override
    def build(self) -> ui.date:
        with (
            ui.input(label=self.label) as date_input,
            ui.menu().props("no-parent-event") as menu,
            ui.date()
            .props("range")
            .bind_value(
                date_input,
                forward=(
                    lambda r: TypeAdapter[DateRange](DateRange).validate_python(r).model_dump()
                    if r is not None
                    else None
                ),
                backward=(
                    lambda i: TypeAdapter[DateRange](DateRange).validate_python(i).dict_dump()
                    if i is not None
                    else None
                ),
            ) as date_picker,
            date_input.add_slot("append"),
        ):
            _ = ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")

        return date_picker

    @override
    @classmethod
    def deserialize(cls, raw: Any) -> DateRange:
        return cls.__type_adapter__.validate_python(raw)

    @override
    @classmethod
    def serialize(cls, value: DateRange) -> Any:
        return value.model_dump()


class JobDetailSearch(BaseModel):
    search_job_description: Annotated[str | None, TextInput(label="搜索职位详情")] = None
    interested_at_between: Annotated[DateRange | None, DateRangeInput(label="筛选💗时间")] = None

    def criteria(self, job_detail_alias: type[JobDetail] = JobDetail) -> sa.BooleanClauseList:
        c = sa.true() & sa.true()

        if self.search_job_description:
            c &= job_detail_alias.job_description.ilike(f"%{self.search_job_description}%")

        return c


class JobDetailParam(JobDetailSearch):
    page: int = 1


@ui.page("/")
async def dashboard(
    # FastAPI 要求最多只能有一个Query Param Model，且只能是 BaseModel 子类
    param: Annotated[JobDetailParam, Query(default_factory=JobDetailParam)],
) -> None: ...


def declare_input[I: BaseModel](
    initial: I,
    on_value_change: Callable[[I], Awaitable[None] | None],
) -> None:
    state = initial.model_copy(deep=True)

    def update_state()

    for field in type(initial).__pydantic_fields__.values():
        metadata_list = [
            cast(Input[ValueElement, Any], m) for m in field.metadata if isinstance(m, Input)
        ]

        if not metadata_list:
            continue

        metadata, *extra = metadata_list

        if extra:
            raise TypeError("Input field can only have one metadata")

        element = metadata.build()
        element.on_value_change(lambda e: on_value_change(initial.mo))




ui.run()
