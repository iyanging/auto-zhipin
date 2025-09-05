from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Self

import fastui
import sqlalchemy as sa
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastui import AnyComponent, FastUI, prebuilt_html
from fastui import components as c
from fastui.components.display import DisplayLookup, DisplayMode
from pydantic import BaseModel, Field, model_validator
from pydantic.fields import FieldInfo

from auto_zhipin.db import JobDetail
from auto_zhipin.deps import db
from auto_zhipin.settings import APP_ROOT


def fti(*, placeholder: str | None = None) -> FieldInfo:
    """Form Text Input."""

    json_schema_extra: dict[str, Any] = {}

    if placeholder is not None:
        json_schema_extra["placeholder"] = placeholder

    return Field(json_schema_extra=json_schema_extra)


@dataclass(kw_only=True)
class TableColumn:
    title: str | None = None
    table_width_percent: int | None = None
    mode: DisplayMode | None = None


def tc(
    *,
    title: str | None,
    mode: DisplayMode | None = None,
    table_width_percent: int | None = None,
) -> "TableColumn":
    return TableColumn(
        title=title,
        mode=mode,
        table_width_percent=table_width_percent,
    )


app = FastAPI()

app.mount("/assets", StaticFiles(directory=APP_ROOT / "assets"))


# FastUI 默认路由规则：
#   页面：/abc -> 调用 -> /api/abc
API_ROOT = "/api"


def api(page_url: str) -> str:
    if not page_url.startswith("/"):
        raise ValueError("Page URL must start with `/`")

    return f"{API_ROOT}{page_url}"


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(PAGE_JOB_LIST)


class JobDetailSearch(BaseModel):
    search_job_description: Annotated[str | None, fti(placeholder="搜索职位详情")] = None

    def criteria(self, job_detail_alias: type[JobDetail] = JobDetail) -> sa.BooleanClauseList:
        c = sa.true() & sa.true()

        if self.search_job_description:
            c &= job_detail_alias.job_description.ilike(f"%{self.search_job_description}%")

        return c


class JobDetailParam(JobDetailSearch):
    page: int = 1


class JobDetailView(BaseModel):
    company_brand_name: Annotated[str, tc(title="公司名称")]
    company_industry_name: Annotated[str, tc(title="行业分类")]

    job_name: Annotated[str, tc(title="职位名称")]
    job_location: Annotated[str, tc(title="工作地")]
    job_experience_name: Annotated[str, tc(title="经验要求")]
    job_degree: Annotated[str, tc(title="学历要求")]
    job_salary_description: Annotated[str, tc(title="薪资待遇")]
    job_description: Annotated[
        str,
        tc(
            title="职位详情",
            mode=DisplayMode.markdown,
            table_width_percent=50,
        ),
    ]


PAGE_JOB_LIST = "/job"


@app.get(api(PAGE_JOB_LIST), response_model=FastUI, response_model_exclude_none=True)
@db.transactional()
async def job_list(
    *,
    # FastAPI 要求 Query Param Model 必须是单参数才能被正确解析
    param: Annotated[JobDetailParam, Query(default_factory=JobDetailParam)],
) -> Sequence[AnyComponent]:
    page_size = 10

    q = sa.select(JobDetail).where(param.criteria())

    q_count = sa.select(sa.func.count()).select_from(q.subquery())
    q_data = (
        q.order_by(JobDetail.created_at.desc())
        .offset((param.page - 1) * page_size)
        .limit(page_size)
    )

    total = (await db.get().execute(q_count)).scalar_one()
    data = (await db.get().execute(q_data)).scalars().all()

    return [
        c.Page(
            components=[
                c.Heading(text="职位详情", level=1),
                c.ModelForm(
                    display_mode="inline",
                    model=JobDetailSearch,
                    submit_on_change=True,
                    initial=param.model_dump(),
                    method="GOTO",
                    submit_url=".",
                ),
                ModeledTable(
                    data_model=JobDetailView,
                    data=[
                        JobDetailView(
                            company_brand_name=d.company_brand_name,
                            company_industry_name=d.company_industry_name,
                            job_name=d.job_name,
                            job_location=(
                                f"{d.job_city_name} "
                                f"{d.job_area_district} "
                                f"{d.job_business_district}"
                            ),
                            job_experience_name=d.job_experience_name,
                            job_degree=d.job_degree,
                            job_salary_description=d.job_salary_description,
                            job_description=d.job_description,
                        )
                        for d in data
                    ],
                ),
                c.Pagination(
                    page=param.page,
                    page_size=page_size,
                    total=total,
                    page_query_param="page",
                ),
            ],
        )
    ]


@app.get("/{_:path}", include_in_schema=False)
async def html_landing(_) -> HTMLResponse:
    # Use local assets instead of CDN
    fastui._PREBUILT_CDN_URL = "/assets"  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
    return HTMLResponse(
        prebuilt_html(
            title="Auto Zhipin",
            api_root_url=API_ROOT,
        )
    )


class ModeledTable(c.Table):
    data_model: type[BaseModel]  # pyright: ignore[reportGeneralTypeIssues, reportIncompatibleVariableOverride]

    def __init__[T: BaseModel](
        self,
        *,
        data_model: type[T],
        data: Sequence[T],
    ) -> None:
        super().__init__(
            data_model=data_model,
            data=data,
        )

    @model_validator(mode="after")
    def _re_fill_columns(self) -> Self:
        # clear existing columns
        self.columns = []

        all_model_fields = {
            **self.data_model.model_fields,
            **self.data_model.model_computed_fields,
        }

        for name, field in all_model_fields.items():
            column_def = (
                next((m for m in field.metadata if isinstance(m, TableColumn)), None)
                if isinstance(field, FieldInfo)
                else None
            )

            if column_def is not None:
                self.columns.append(
                    DisplayLookup(
                        field=name,
                        mode=column_def.mode,
                        title=column_def.title,
                        table_width_percent=column_def.table_width_percent,
                    )
                )

            else:
                self.columns.append(
                    DisplayLookup(
                        field=name,
                        title=field.title,
                    )
                )

        return self
