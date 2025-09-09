from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    NotRequired,
    Self,
    TypedDict,
    cast,
    override,
)

import pendulum
import sqlalchemy as sa
from annotated_types import Ge
from fastapi import Query
from fastapi.datastructures import QueryParams
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
from pydantic.fields import FieldInfo

from auto_zhipin.db import JobDetail
from auto_zhipin.deps import db


class InputField[E: ValueElement, T](ABC):
    @abstractmethod
    def build(self, field_info: FieldInfo) -> E: ...

    @abstractmethod
    def deserialize(self, raw: Any | None) -> T | None: ...

    @abstractmethod
    def serialize(self, value: T | None) -> Any | None: ...


class InputGroup(BaseModel, revalidate_instances="always"): ...


@dataclass(kw_only=True)
class TextInput(InputField[ui.input, str]):
    label: str | None = None
    placeholder: str | None = None

    @override
    def build(self, field_info: FieldInfo) -> ui.input:
        return ui.input(
            label=self.label,
            placeholder=self.placeholder,
        )

    @override
    def deserialize(self, raw: Any | None) -> str | None:
        return str(raw) if raw is not None and raw != "" else None  # noqa: PLC1901

    @override
    def serialize(self, value: str | None) -> Any | None:
        return value if value is not None and value != "" else None  # noqa: PLC1901


type _DateRangeDict = dict[Literal["from"] | Literal["to"], str | None]


class _DateRangeStr(RootModel[str]):
    __separator__: ClassVar[str] = " - "

    _from: date | None
    _to: date | None

    @model_validator(mode="after")
    def _init(self) -> Self:
        groups = [
            (date.fromisoformat(s) if (s := g.strip()) else None)
            for g in self.root.split(self.__separator__, 1)
        ]

        if len(groups) == 2:  # noqa: PLR2004
            self._from, self._to = groups

        elif len(groups) == 1:
            self._from, self._to = groups[0], None

        else:
            self._from, self._to = None, None

        return self

    @property
    def from_(self) -> date | None:
        return self._from

    @property
    def to(self) -> date | None:
        return self._to

    def dict_dump(self) -> _DateRangeDict:
        return {
            "from": self.from_ and self.from_.isoformat(),
            "to": self.to and self.to.isoformat(),
        }

    @override
    def __eq__(self, value: "DateRange | Any") -> bool:
        return isinstance(value, DateRange) and self.dict_dump() == value.dict_dump()

    @override
    def __hash__(self):
        return hash((self.from_, self.to))

    if TYPE_CHECKING:

        @override
        def model_dump(self, **_: Any) -> str: ...


class _DateRangeStruct(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_: Annotated[date | None, Field(alias="from")]
    to: date | None

    @model_serializer(mode="plain")
    def __serialize__(self) -> str:
        return f"{self.from_ or ''}{_DateRangeStr.__separator__}{self.to or ''}"

    @model_validator(mode="before")
    @classmethod
    def __deserialize__(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        data = cast(dict[Any, Any], data.copy())

        if "from" in data:
            data["from"] = data["from"] or None

        if "to" in data:
            data["to"] = data["to"] or None

        return data

    def dict_dump(self) -> _DateRangeDict:
        return {
            "from": self.from_ and self.from_.isoformat(),
            "to": self.to and self.to.isoformat(),
        }

    @override
    def __eq__(self, value: "DateRange | Any") -> bool:
        return isinstance(value, DateRange) and self.dict_dump() == value.dict_dump()

    @override
    def __hash__(self):
        return hash((self.from_, self.to))

    if TYPE_CHECKING:

        @override
        def model_dump(self, **_: Any) -> str: ...  # pyright: ignore[reportIncompatibleMethodOverride]


DateRange = _DateRangeStruct | _DateRangeStr


@dataclass(kw_only=True)
class DateRangeInput(InputField[ui.date, DateRange]):
    __type_adapter__: ClassVar[TypeAdapter[DateRange]] = TypeAdapter[DateRange](DateRange)

    label: str | None = None

    @override
    def build(self, field_info: FieldInfo) -> ui.date:
        with (
            ui.input(label=self.label) as date_input,
            ui.menu().props("no-parent-event") as menu,
            ui.date()
            .props("range")
            .bind_value(
                date_input,
                forward=(lambda r: self.serialize(self.deserialize(r))),
                backward=(lambda i: (d := self.deserialize(i)) and d.dict_dump()),
            ) as date_picker,
            date_input.add_slot("append"),
        ):
            _ = ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")

        return date_picker

    @override
    def deserialize(self, raw: Any | None) -> DateRange | None:
        if raw is not None and raw != "":  # noqa: PLC1901
            return self.__type_adapter__.validate_python(raw)

        else:
            return None

    @override
    def serialize(self, value: DateRange | None) -> Any | None:
        return value.model_dump() if value is not None else None


@dataclass(kw_only=True)
class SelectInput(InputField[ui.select, Any]):
    label: str | None = None
    options: dict[str, Any] | list[str]

    @override
    def build(self, field_info: FieldInfo) -> ui.select:
        return ui.select(
            self.options,
            label=self.label,
            clearable=field_info.is_required(),
        )

    @override
    def deserialize(self, raw: Any | None) -> Any | None:
        if not isinstance(raw, str):
            raise TypeError("Select input value must be string")

        if isinstance(self.options, list):
            if raw not in self.options:
                raise ValueError(f"Invalid select input key: {raw}")

            return raw

        else:
            if raw not in self.options:
                raise ValueError(f"Invalid select input key: {raw}")

            return self.options[raw]

    @override
    def serialize(self, value: Any | None) -> Any | None:
        if isinstance(self.options, list):
            if value not in self.options:
                raise ValueError(f"Invalid select input value: {value}")

            return value

        else:
            for k, v in self.options.items():
                if v == value:
                    return k

            raise ValueError(f"Invalid select input value: {value}")


class ColumnDefinition(TypedDict):
    name: str
    label: str
    field: str
    required: NotRequired[bool | None]
    align: NotRequired[Literal["left", "center", "right"] | None]
    classes: NotRequired[str | None]
    headerStyle: NotRequired[str | None]
    headerClasses: NotRequired[str | None]


class TableColumn[S](ABC):
    @abstractmethod
    def format_value(self, value: S | None) -> str | None: ...

    def build(  # noqa: PLR6301
        self,
        field_name: str,
        field_info: FieldInfo,
    ) -> ColumnDefinition:
        return {
            "name": field_info.alias or field_name,
            "label": field_info.title or field_info.alias or field_name,
            "field": field_info.alias or field_name,
            "required": field_info.is_required(),
        }


class IdColumn(TableColumn[int | str]):
    @override
    def format_value(self, value: int | str | None) -> str | None:
        return str(value) if value is not None else None

    @override
    def build(
        self,
        field_name: str,
        field_info: FieldInfo,
    ) -> ColumnDefinition:
        d = super().build(field_name, field_info)
        d.update(
            {
                "classes": "hidden",
                "headerClasses": "hidden",
            }
        )
        return d


class TextColumn(TableColumn[str]):
    @override
    def format_value(self, value: str | None) -> str | None:
        return value


class DateTimeColumn(TableColumn[datetime]):
    format: str = "%Y-%m-%d %H:%M:%S"
    timezone: str = "Asia/Shanghai"

    @override
    def format_value(self, value: datetime | None) -> str | None:
        if value is None:
            return None

        return (
            pendulum.instance(value, self.timezone)
            .in_timezone(self.timezone)
            .strftime(self.format)
        )


class JobDetailSearch(InputGroup):
    search_job_description: Annotated[str | None, TextInput(label="搜索职位详情")] = None
    interested_at_between: Annotated[DateRange | None, DateRangeInput(label="筛选💗时间")] = None

    def criteria(self, job_detail_alias: type[JobDetail] = JobDetail) -> sa.BooleanClauseList:
        c = sa.true() & sa.true()

        if self.search_job_description:
            c &= job_detail_alias.job_description.ilike(f"%{self.search_job_description}%")

        return c


class JobDetailParam(JobDetailSearch):
    page: int = 1
    page_size: int = 10

    def update_pagination(self, pagination: "Pagination") -> Self:
        new = self.model_copy(deep=True)
        new.page_size = pagination.rows_per_page
        new.page = pagination.page

        return type(self).model_validate(new)


class JobDetailView(BaseModel):
    company_brand_name: Annotated[str, TextColumn()] = Field(title="公司名称")
    company_industry_name: Annotated[str, TextColumn()] = Field(title="行业分类")

    job_encrypt_id: Annotated[str, IdColumn()]
    job_name: Annotated[str, TextColumn()] = Field(title="职位名称")
    job_location: Annotated[str, TextColumn()] = Field(title="工作地")
    job_experience_name: Annotated[str, TextColumn()] = Field(title="经验要求")
    job_degree: Annotated[str, TextColumn()] = Field(title="学历要求")
    job_salary_description: Annotated[str, TextColumn()] = Field(title="薪资待遇")
    job_description: Annotated[str, TextColumn()] = Field(title="职位详情")


@ui.page("/")
@db.transactional()
async def dashboard(
    # FastAPI 要求最多只能有一个Query Param Model，且只能是 BaseModel 子类
    param: Annotated[JobDetailParam, Query(default_factory=JobDetailParam)],
) -> None:
    q = sa.select(JobDetail).where(param.criteria())

    q_count = sa.select(sa.func.count()).select_from(q.subquery())
    q_data = (
        q.order_by(JobDetail.created_at.desc())
        .offset((param.page - 1) * param.page_size)
        .limit(param.page_size if param.page_size > 0 else None)
    )

    total = (await db.get().execute(q_count)).scalar_one()
    data = (await db.get().execute(q_data)).scalars().all()

    with ui.column().classes("w-full items-center"):
        with ui.row(align_items="center"):
            new_param = param.model_copy(deep=True)

            def update_new_param(p: JobDetailParam) -> None:
                nonlocal new_param
                new_param = p

            declare_input(param, on_value_change=update_new_param)

            _ = ui.button(
                "搜索",
                on_click=(
                    lambda: ui.navigate.to(
                        ui.context.client.page.path + f"?{build_query_string(new_param)}"
                    )
                ),
                icon="search",
            )

        with ui.row(align_items="center"):
            declare_table(
                JobDetailView,
                [
                    JobDetailView(
                        company_brand_name=d.company_brand_name,
                        company_industry_name=d.company_industry_name,
                        job_encrypt_id=d.job_encrypt_id,
                        job_name=d.job_name,
                        job_location=(
                            f"{d.job_city_name} {d.job_area_district} {d.job_business_district}"
                        ),
                        job_experience_name=d.job_experience_name,
                        job_degree=d.job_degree,
                        job_salary_description=d.job_salary_description,
                        job_description=d.job_description,
                    )
                    for d in data
                ],
                Pagination(
                    rowsNumber=total,
                    rowsPerPage=param.page_size,
                    page=param.page,
                ),
                on_pagination_change=(
                    lambda p: ui.navigate.to(
                        ui.context.client.page.path
                        + f"?{build_query_string(param.update_pagination(p))}"
                    )
                ),
            )


def declare_input[IG: InputGroup](
    initial: IG,
    on_value_change: Callable[[IG], Awaitable[Any] | Any],
) -> None:
    state = initial.model_copy(deep=True)

    def update_state(
        field_name: str,
        input_field: InputField[ValueElement, Any],
        value: Any,
    ) -> Awaitable[None] | None:
        nonlocal state

        try:
            parsed = input_field.deserialize(value)

        except Exception:  # noqa: BLE001
            # invalid value, do nothing
            return

        else:
            if getattr(state, field_name) == parsed:
                # same value as before, do nothing
                return

            state = type(state).model_validate(state.model_copy(update={field_name: parsed}))

            return on_value_change(state)

    for field_name, field_info in type(initial).__pydantic_fields__.items():
        initial_field_value = getattr(initial, field_name)

        metadata_list = [
            cast(InputField[ValueElement, Any], m)
            for m in field_info.metadata
            if isinstance(m, InputField)
        ]

        if not metadata_list:
            continue

        input_field, *extra = metadata_list

        if extra:
            raise TypeError("Input field can only have one metadata")

        element = input_field.build(field_info)
        element.set_value(input_field.serialize(initial_field_value))
        _ = element.on_value_change(
            lambda e, field_name=field_name, input_field=input_field: update_state(
                field_name,
                input_field,
                e.value,
            )
        )


def build_query_string(param: BaseModel) -> str:
    item_dict = param.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
        exclude_none=True,
        exclude_defaults=True,
    )

    # flatten list values
    item_list = [
        (k, i)
        for k, v in item_dict.items()
        for i in (cast(list[Any], v) if isinstance(v, list) else [v])
    ]

    return str(QueryParams(item_list))


class Pagination(BaseModel):
    rows_number: int = Field(alias="rowsNumber", ge=0)
    rows_per_page: Literal[0] | Annotated[int, Ge(1)] = Field(alias="rowsPerPage")
    page: int = Field(ge=1)


def declare_table[M: BaseModel](
    model: type[M],
    data: Sequence[M],
    pagination: Pagination,
    on_pagination_change: Callable[[Pagination], Awaitable[Any] | Any],
) -> None:
    columns: list[ColumnDefinition] = []
    id_column: ColumnDefinition | None = None

    for field_name, field_info in model.__pydantic_fields__.items():
        metadata_list = [
            cast(TableColumn[Any], m) for m in field_info.metadata if isinstance(m, TableColumn)
        ]

        if not metadata_list:
            continue

        table_column, *extra = metadata_list

        if extra:
            raise TypeError("Table column can only have one metadata")

        column_def = table_column.build(field_name, field_info)
        columns.append(column_def)

        if isinstance(table_column, IdColumn):
            if id_column is not None:
                raise ValueError("Table can only have one IdColumn")

            id_column = column_def

    rows = [d.model_dump(mode="json", by_alias=True) for d in data]

    def on_request(e: dict[str, Any]) -> Awaitable[Any] | Any:
        new_page = Pagination.model_validate(e["pagination"])

        if new_page != pagination:
            return on_pagination_change(new_page)

    table = ui.table(
        columns=columns,  # pyright: ignore[reportArgumentType]
        rows=rows,
        row_key=(id_column["name"] if id_column else ""),
        pagination=pagination.model_dump(mode="json", by_alias=True),
    )

    _ = table.on("request", lambda e: on_request(e.args))
