import asyncio
import logging
import random
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from types import TracebackType
from typing import ClassVar, Literal, cast, override

from browserforge.fingerprints import Screen
from camoufox.async_api import AsyncCamoufox
from playwright._impl._api_structures import SetCookieParam  # noqa: PLC2701
from playwright.async_api import (
    Browser,
    BrowserContext,
    expect,
)
from playwright.async_api import Cookie as PlaywrightCookie
from playwright.async_api import Request as PlaywrightRequest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field, TypeAdapter
from yarl import URL

from auto_zhipin.db import Cookie, JobDetail


class RawJobInfo(BaseModel):
    encrypt_id: str = Field(alias="encryptId")
    job_name: str = Field(alias="jobName")
    position_name: str = Field(alias="positionName")
    location_name: str = Field(alias="locationName")
    experience_name: str = Field(alias="experienceName")
    degree_name: str = Field(alias="degreeName")
    salary_desc: str = Field(alias="salaryDesc")
    post_description: str = Field(alias="postDescription")
    address: str
    show_skills: list[str] = Field(alias="showSkills")
    job_status_desc: str = Field(alias="jobStatusDesc")


class RawBossInfo(BaseModel):
    name: str
    title: str
    active_time_desc: str = Field(alias="activeTimeDesc")
    brand_name: str = Field(alias="brandName")


class RawBrandComInfo(BaseModel):
    encrypt_brand_id: str = Field(alias="encryptBrandId")
    brand_name: str = Field(alias="brandName")
    stage_name: str = Field(alias="stageName")
    scale_name: str = Field(alias="scaleName")
    industry_name: str = Field(alias="industryName")
    introduce: str
    labels: list[str]
    customer_brand_name: str = Field(alias="customerBrandName")
    customer_brand_stage_name: str = Field(alias="customerBrandStageName")


class RawJobDetail(BaseModel):
    security_id: str = Field(alias="securityId")
    job_info: RawJobInfo = Field(alias="jobInfo")
    boss_info: RawBossInfo = Field(alias="bossInfo")
    brand_com_info: RawBrandComInfo = Field(alias="brandComInfo")


async def default_job_filter(job: RawJobDetail) -> bool:  # noqa: RUF029
    # 过滤BOSS活跃状态
    return not set(job.boss_info.active_time_desc) & {"周月年"}


async def default_interval_delayer() -> None:
    # 模拟人类阅读耗时
    await asyncio.sleep(random.randint(3000, 5000) / 1000)  # noqa: S311


class BossZhipin(AbstractAsyncContextManager["BossZhipin"]):
    logger: ClassVar[logging.Logger] = logging.getLogger(__qualname__)

    base_url: URL
    headless: bool
    allow_to_show_login_page: bool
    wait_for_login_timeout_in_sec: int

    playwright: AsyncCamoufox
    browser: Browser | None
    browser_ctx: BrowserContext | None

    def __init__(
        self,
        *,
        base_url: str = "https://www.zhipin.com",
        headless: bool = True,
        allow_to_show_login_page: bool = True,
        wait_for_login_timeout_in_ms: int = 3 * 60,
    ) -> None:
        super().__init__()

        self.base_url = URL(base_url)
        self.headless = headless
        self.allow_to_show_login_page = allow_to_show_login_page
        self.wait_for_login_timeout_in_sec = wait_for_login_timeout_in_ms

        self.playwright = self._playwright_ctx(headless=headless)
        self.browser = None
        self.browser_ctx = None

    async def _get_browser_ctx(self) -> BrowserContext:
        if self.browser is None:
            self.browser = cast(Browser, await self.playwright.__aenter__())  # noqa: PLC2801

            self.logger.info(
                "Playwright [headless=%s] browser bootstrapped",
                self.headless,
            )

        if self.browser_ctx is None:
            self.browser_ctx = await self.browser.new_context()

        return self.browser_ctx

    @override
    async def __aenter__(self) -> "BossZhipin":
        _ = await self._get_browser_ctx()  # cache
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self.logger.info("Playwright browser is shutting down")

        if self.browser_ctx is not None:
            await self.browser_ctx.__aexit__(exc_type, exc_value, traceback)
            self.browser_ctx = None

        if self.browser is not None:
            await self.browser.__aexit__(exc_type, exc_value, traceback)
            self.browser_ctx = None

    async def login(self, cookies: Sequence[Cookie]) -> Sequence[Cookie]:
        unmarshaled_cookies = [self._unmarshal_cookie(cookie) for cookie in cookies]

        # 加载之前存下来的cookies
        # 若之前已登录且token未过期，则本次运行可免登录
        ctx = await self._get_browser_ctx()
        await ctx.add_cookies(unmarshaled_cookies)

        # 前往登录页
        async with await ctx.new_page() as page:
            _ = await page.goto(
                self._get_login_url(),
                # 经过试验，使用 networkidle 才能让goto()在登录页自动跳转后再返回
                wait_until="networkidle",
            )

            if URL(page.url) == self.base_url:  # 若已登录，则跳转首页
                self.logger.info("User is logged in by cookies")

                return cookies  # 无需登录

        # 重开一个页面，供人工登录

        self.logger.info("Cannot login by cookies, try to open a visible login page")

        if not self.allow_to_show_login_page:
            raise BossZhipinError("Login page show is disabled while user is needed to login")

        if self.headless:
            # headless模式需要重开新的可见的浏览器
            self.logger.info("Open a new / visible browser for user login")

            async with (
                self._playwright_ctx(headless=False) as browser,
                await cast(Browser, browser).new_context() as ctx,
            ):
                raw_cookies = await self._wait_for_login(ctx)

        else:
            # 可以复用浏览器
            self.logger.info("Reuse the inner visible browser for user login")

            ctx = await self._get_browser_ctx()
            raw_cookies = await self._wait_for_login(ctx)

        del ctx

        marshaled_cookies = [self._marshal_cookie(cookie) for cookie in raw_cookies]

        # 导入cookies到内部的浏览器
        unmarshaled_cookies = [self._unmarshal_cookie(cookie) for cookie in marshaled_cookies]
        await (await self._get_browser_ctx()).add_cookies(unmarshaled_cookies)

        self.logger.info("Cookies extracted")

        return marshaled_cookies

    async def _wait_for_login(self, ctx: BrowserContext) -> list[PlaywrightCookie]:
        async with await ctx.new_page() as page:
            _ = await page.goto(
                self._get_login_url(),
                wait_until="load",
            )

            self.logger.info(
                "Loaded login page, wait login for %s seconds",
                self.wait_for_login_timeout_in_sec,
            )

            # 若登录成功，则会跳转回首页，顶栏右上角会出现用户头像
            try:
                _ = await page.wait_for_selector(
                    ".nav-figure",
                    state="visible",
                    timeout=self.wait_for_login_timeout_in_sec * 1000,
                )

            except PlaywrightTimeoutError as e:
                raise BossZhipinError("Timeout while waiting for user login") from e

            # 成功登录
            self.logger.info("User logged in")

            # 导出cookies
            return await ctx.cookies()

    async def seek_jobs(
        self,
        from_url: str,
        count: int,
        *,
        filter_func: Callable[[RawJobDetail], Awaitable[bool]] = default_job_filter,
        interval_func: Callable[[], Awaitable[None]] = default_interval_delayer,
    ) -> AsyncGenerator[JobDetail]:
        ctx = await self._get_browser_ctx()

        encrypt_job_id_to_job_summary: dict[str, RawJobSummary] = {}
        job_detail_list: list[RawJobDetail] = []

        async def on_request_finished(req: PlaywrightRequest) -> None:
            # 保存左侧职位列表的响应
            if req.url.startswith(self._get_job_list_url_prefix()):
                job_list_resp = await self._parse_response(req, RawJobListResponse)

                for job_summary in job_list_resp.zp_data.job_list:
                    encrypt_job_id_to_job_summary[job_summary.encrypt_job_id] = job_summary

            # 保存右侧职位详情的响应
            elif req.url.startswith(self._get_job_detail_url_prefix()):
                job_detail_resp = await self._parse_response(req, RawJobDetailResponse)
                job_detail = job_detail_resp.zp_data

                job_detail_list.append(job_detail)

        async with await ctx.new_page() as page:
            page.on("requestfinished", on_request_finished)

            _ = await page.goto(
                from_url,
                wait_until="load",
            )

            # 左侧可以滚动的职位列表
            job_list_con = page.locator(".job-list-container")
            await expect(job_list_con).to_be_visible()

            # 职位列表拉取下一页的动画
            loading = job_list_con.locator(".loading-wait")

            queried_count = 0
            job_list_ix = 0

            while queried_count < count:
                # 职位列表滚动到接近底部时，会触发下一页的拉取，需要等待完成
                await expect(loading).to_be_hidden()

                # 职位列表下一页会拼接到当前页的尾部
                # 所以每次重新获取职位列表时，可以保证已访问过的职位在列表中的索引不变
                job_list = await job_list_con.locator(".job-card-box").all()

                # 通过点击左侧职位，触发右侧职位详情的拉取
                await job_list[job_list_ix].click()

                # 立即更新 ix
                job_list_ix += 1

                # 构造 job
                job_detail = job_detail_list[-1]

                if await filter_func(job_detail):
                    job_summary = encrypt_job_id_to_job_summary[job_detail.job_info.encrypt_id]
                    job = self._build_job_detail(job_summary, job_detail)

                    yield job

                await interval_func()

    # async def apply_jobs(
    #     self, jobs: list[dict[str, str]]
    # ) -> AsyncGenerator[HrDialog, None]:
    #     async with async_playwright() as p:
    #         browser = await p.chromium.launch(
    #             headless=True if self._headless_cb else False,
    #             args=["--disable-blink-features=AutomationControlled"],
    #         )
    #         context = await browser.new_context()
    #         page = await context.new_page()
    #         if not await login(context, page, self._cookies_path, self._headless_cb):
    #             return
    #         for job in jobs:
    #             job_info = Job.Info.model_validate(job)
    #             await page.goto(f"{base_url}{job_info.url}", wait_until="networkidle")
    #             primary = page.locator(".info-primary")
    #             await expect(primary).to_be_visible()
    #             apply = primary.get_by_role("link", name="立即沟通")
    #             if await apply.is_visible():
    #                 await apply.click(delay=random.randint(32, 512))
    #                 dialog = page.locator(".dialog-container")
    #                 await expect(dialog).to_be_visible()
    #                 yield HrDialog(job_info, dialog)

    def _get_login_url(self) -> str:
        return str(self.base_url / "web/user/" % "ka=header-login")

    def _get_job_list_url_prefix(self) -> str:
        return str(self.base_url / "wapi/zpgeek/pc/recommend/job/list.json")

    def _get_job_detail_url_prefix(self) -> str:
        return str(self.base_url / "wapi/zpgeek/job/detail.json")

    @staticmethod
    def _marshal_cookie(cookie: PlaywrightCookie) -> Cookie:
        if "name" not in cookie or "value" not in cookie:
            raise BossZhipinError(f"Ill-formed cookie: {cookie}")

        return Cookie(
            name=cookie["name"],
            value=cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path"),
            expires=(Decimal(str(cookie["expires"])) if "expires" in cookie else None),
            http_only=cookie.get("httpOnly"),
            secure=cookie.get("secure"),
            same_site=cookie.get("sameSite"),
            partition_key=cookie.get("partitionKey"),
        )

    @staticmethod
    def _unmarshal_cookie(cookie: Cookie) -> SetCookieParam:
        set_cookie = SetCookieParam(
            name=cookie.name,
            value=cookie.value,
            # url=None, # DO NOT set url
        )

        if cookie.domain is not None:
            set_cookie["domain"] = cookie.domain
        if cookie.path is not None:
            set_cookie["path"] = cookie.path
        if cookie.expires is not None:
            set_cookie["expires"] = float(cookie.expires)
        if cookie.http_only is not None:
            set_cookie["httpOnly"] = cookie.http_only
        if cookie.secure is not None:
            set_cookie["secure"] = cookie.secure
        if cookie.same_site is not None:
            set_cookie["sameSite"] = cookie.same_site
        if cookie.partition_key is not None:
            set_cookie["partitionKey"] = cookie.partition_key

        return set_cookie

    @staticmethod
    async def _parse_response[T: BaseModel](request: PlaywrightRequest, type_: type[T]) -> T:
        resp = await request.response()
        if resp is None or not resp.ok:
            raise ProgrammingError("Error response should not be parsed")

        body = await resp.body()

        return TypeAdapter(type_).validate_json(body)

    @staticmethod
    def _build_job_detail(job_summary: "RawJobSummary", job_detail: "RawJobDetail") -> JobDetail:
        return JobDetail(
            company_encrypt_brand_id=job_detail.brand_com_info.encrypt_brand_id,
            company_brand_name=job_detail.brand_com_info.brand_name,
            company_stage_name=job_detail.brand_com_info.stage_name,
            company_scale_name=job_detail.brand_com_info.scale_name,
            company_industry_name=job_detail.brand_com_info.industry_name,
            company_introduce=job_detail.brand_com_info.introduce,
            job_encrypt_id=job_detail.job_info.encrypt_id,
            job_name=job_detail.job_info.job_name,
            job_city_name=job_summary.city_name,
            job_area_district=job_summary.area_district,
            job_business_district=job_summary.business_district,
            job_address=job_detail.job_info.address,
            job_experience_name=job_detail.job_info.experience_name,
            job_degree=job_detail.job_info.degree_name,
            job_salary_description=job_detail.job_info.salary_desc,
            job_description=job_detail.job_info.post_description,
        )

    @staticmethod
    def _playwright_ctx(*, headless: bool | Literal["virtual"]) -> AsyncCamoufox:
        return AsyncCamoufox(
            os="windows",
            screen=Screen(max_width=1920, max_height=1080),
            locale="zh-CN",
            humanize=True,
            headless=headless,
        )


class BossZhipinError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProgrammingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class RawJobSummary(BaseModel):
    security_id: str = Field(alias="securityId")
    boss_name: str = Field(alias="bossName")
    boss_title: str = Field(alias="bossTitle")
    encrypt_job_id: str = Field(alias="encryptJobId")
    job_name: str = Field(alias="jobName")
    salary_desc: str = Field(alias="salaryDesc")
    job_labels: list[str] = Field(alias="jobLabels")
    skills: list[str]
    job_experience: str = Field(alias="jobExperience")
    job_degree: str = Field(alias="jobDegree")
    city_name: str = Field(alias="cityName")
    area_district: str = Field(alias="areaDistrict")
    business_district: str = Field(alias="businessDistrict")
    encrypt_brand_id: str = Field(alias="encryptBrandId")
    brand_name: str = Field(alias="brandName")
    brand_stage_name: str = Field(alias="brandStageName")
    brand_industry: str = Field(alias="brandIndustry")
    brand_scale_name: str = Field(alias="brandScaleName")


class RawJobList(BaseModel):
    has_more: bool = Field(alias="hasMore")
    job_list: list[RawJobSummary] = Field(alias="jobList")


class RawJobListResponse(BaseModel):
    zp_data: RawJobList = Field(alias="zpData")


class RawJobDetailResponse(BaseModel):
    zp_data: RawJobDetail = Field(alias="zpData")
