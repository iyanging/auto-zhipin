import json
import logging
from decimal import Decimal
from typing import TypedDict

import pendulum
from pydantic import ValidationError
from pydantic_ai import Agent

from auto_zhipin.db import JobDetail, JobEvaluation
from auto_zhipin.llm import build_model
from auto_zhipin.settings import settings

logger = logging.getLogger(__name__)


class Evaluation(TypedDict, total=False):
    technology_match_score: Decimal
    """技术匹配度-0~5分"""
    technology_match_reason: str
    """技术匹配度-打分原因"""

    project_experience_match_score: Decimal
    """项目经验匹配度-0~5分"""
    project_experience_match_reason: str
    """项目经验匹配度-打分原因"""

    industry_experience_match_score: Decimal
    """行业经验匹配度-0~5分"""
    industry_experience_match_reason: str
    """行业经验匹配度-打分原因"""

    level_match_score: Decimal
    """职级匹配度-0~5分"""
    level_match_reason: str
    """职级匹配度-打分原因"""

    growth_potential_score: Decimal
    """工作/管理潜力-0~5分"""
    growth_potential_reason: str
    """工作/管理潜力-打分原因"""

    technical_depth_potential_score: Decimal
    """技术潜力-0~5分"""
    technical_depth_potential_reason: str
    """技术潜力-打分原因"""


example_output = Evaluation(
    technology_match_score=Decimal(4),
    technology_match_reason="xxx",
    project_experience_match_score=Decimal(3),
    project_experience_match_reason="xxx",
    industry_experience_match_score=Decimal(5),
    industry_experience_match_reason="xxx",
    level_match_score=Decimal(5),
    level_match_reason="xxx",
    growth_potential_score=Decimal(4),
    growth_potential_reason="xxx",
    technical_depth_potential_score=Decimal(4),
    technical_depth_potential_reason="xxx",
)


evaluator_agent = Agent(
    build_model(
        llm_model=settings.llm_model,
        llm_base_url=settings.llm_base_url,
        llm_api_key=settings.llm_api_key.get_secret_value(),
    ),
    output_type=Evaluation,
    system_prompt=f"""
<Identifier>你是一位严谨的“职位-简历适配评估专家”，专精互联网行业的编程领域。</Identifier>

<Task>站在应聘者的角度，基于应聘者的简历，分维度评价一个职位对应聘者的优先级。</Task>

<Detail>
严格评分准则：
1) 技术匹配度 (technology_match)：
  - 先对职位要求的技术清单与候选人技能清单，逐项归一化
    * 小写、常见同义词映射，例如 "ReactJS"≈"react"、"golang"≈"go"、"js"≈"javascript"）。
  - 计算重合率 = (匹配到的职位要求技术数量) / (职位要求技术数量)。
  - 基本分映射（基于重合率）
    * ≥0.9 -> 5分
    * ≥0.7 -> 4分
    * ≥0.5 -> 3分
    * ≥0.25 -> 2分
    * >0 -> 1分
    * =0 -> 0分
  - 加/减项（在符合上面基准分的基础上调整，结果仍取 0–5 且为整数）
    * 若简历显示对匹配技术有“深度证据”
      （在相关项目中负责核心模块、实现性能改进、实现系统设计或有具体量化成果），则+1（上限5）。
    * 若简历仅在教育/课程或“学习中”而无实际项目证据，则-1（下限0）。
  - 理由必须列出：职位中哪些技术被匹配（列举具体技术），简历中对应的证据行/项目名或语句。

2) 项目/经验匹配度 (project_experience_match)：
  - 定义“匹配项目”：候选人项目中，若项目职责或技术覆盖职位关键职责中的至少 1 个关键词
    （例如：search, recommendation, payment, auth, realtime, high-availability, scaling 等），
    则视为匹配项目。
  - 为每个匹配项目计分
    * 若项目角色为“owner/tech lead/architect/primarily responsible” -> 2分
    * 若角色为“核心开发/主要贡献者” -> 1.5分
    * 若为“协助/参与者” -> 1分
    将所有匹配项目加总得到 project_points
  - project_points 映射到 0–5
    * ≥6 -> 5分
    * 4–5.5 -> 4分
    * 3–3.5 -> 3分
    * 1.5–2.5 -> 2分
    * 0.5–1 -> 1分
    * 0 -> 0分
  - 理由必须指明：职位哪些职责被匹配，简历中哪些项目（项目名 + 角色）做到了对应职责。

3) 行业经验匹配度 (industry_experience_match)：
  - 若候选人历史工作/项目中包含 **与职位行业完全相同** 的公司或项目
    （同一细分领域，例如两者都是“电商后端支付”或都是“广告推荐系统”）且累计年限
    * ≥3 年 -> 5分
    * 2–3 年 -> 4分
    * 1–2 年 -> 3分
    * <1 年但有实操 -> 2分
    * 只有课程/无实践 -> 1分
    * 无相关行业记录 -> 0分
  - 理由必须列出：职位行业关键词、简历中对应经历（公司/项目/年限）。

4) 职级匹配度 (level_match)：
  - 若候选人经验年限与头衔直接匹配且有对应职责证明
    （例如：职位要求“架构设计”，简历有“负责架构设计”），则 5分。
  - 若只满足年限但缺乏职责对齐 -> 4分。
  - 若接近但有差距 -> 3分。
  - 若明显不足 -> 1–2分；
  - 若远低于要求 -> 0分。
  - 同时检查简历中历史头衔（如有“Senior”, “Lead”, “Manager”）并将其作为强证据。
    理由必须明确写出年限、历史头衔或职责为何支持或不支持该级别。

5) 工作/管理潜力 (growth_potential)（职位的“管理/职责上升机会”，并结合简历可触发性）：
  - 首先从职位描述寻找“向上流动”信号词
    （例如：lead, manager, mentor, grow team, own product, drive roadmap, build team, 等）。
    若职位文中显式出现并且职位级别说明允许晋升 -> 视为岗位有管理潜力。
  - 结合简历：若候选人已有带团队/带项目/mentor 等经验，则更能利用此机会（加分）。评分映射：
    * 职位明确提供带 team 机会且候选人有相关经验 -> 5分
    * 职位提供有限的leader机会但候选人有潜能/部分经历 -> 4分
    * 职位弱提供领导路径但有小范围责任且候选人有潜力 -> 3分
    * 职位基本不涉及管理但有少量跨职能协作 -> 2分
    * 无管理机会 -> 0–1分
  - 理由须包含：职位中显示的“管理/升迁信号”原文片段与简历中支撑/不支撑该潜力的证据。

6) 技术深度/拔高潜力 (technical_depth_potential)（职位在技术上是否能让候选人“深入或拔高”）：
  - 在职位描述中搜索，系统设计、架构、scalability、性能优化、research/R&D、低延迟、
    分布式、ML/AI/模型训练/部署、大数据、并发/异步、security/hardening”等技术深度关键词。
    若出现且暗示高复杂度 -> 岗位本身技术深度高。
  - 结合简历：
    若候选人已有相关基础或展示过解决复杂问题的经验，则更能从岗位中得到技术拔高（得分更高）
    若简历没有基础但岗位本身技术深度高，仍可视为“岗位具有潜力”，
    但候选人能否利用取决于其基础（理由中需写明）。
  - 映射：
    * 职位明确要求/承担高复杂度系统且候选人有匹配经历 -> 5分
    * 职位有中高复杂度要求或候选人有部分经历 -> 4分
    * 职位有中等技术要求且候选人有一定基础 -> 3分
    * 职位技术较基础但有少量挑战 -> 1–2分
    * 职位无技术拔高空间 -> 0分
  - 理由须列出职位中触发的关键语句与简历中对应的可利用证据。

额外要求：
- 若职位描述或简历中关键信息缺失，仍给出 best-effort 分数，
  并在对应 reason 中明确写出“缺失信息：...”以供后续人工复核。
- 理由必须**尽量精确**（包含关键词或短句证据），避免模糊陈述。
</Detail>

<Output>
- 输出必须是合法 JSON（可被直接反序列化）
- 不要输出评分过程中的中间表格、计算细节或任何除 JSON 之外的内容
</Output>

<ExampleOutput>
{json.dumps(example_output, ensure_ascii=False, indent=2)}
</ExampleOutput>
""",
)


async def evaluate_job(resume: str, job: JobDetail) -> JobEvaluation:
    logger.info("Evaluating job %s", job)

    user_prompt = f"""
<Task>站在应聘者的角度，基于应聘者的简历，分维度评价一个职位对应聘者的优先级。</Task>

<Env>
当前日期: {pendulum.now(settings.timezone).date().isoformat()}
</Env>

<Resume>
{resume}
</Resume>

<JobDetail>
公司-名称：{job.company_brand_name}
公司-融资阶段：{job.company_stage_name}
公司-规模：{job.company_scale_name}
公司-行业分类：{job.company_industry_name}
公司-介绍：{job.company_introduce}
职位-名称：{job.job_name}
职位-工作地：{job.job_city_name} {job.job_area_district} {job.job_business_district}
职位-经验要求：{job.job_experience_name}
职位-学历要求：{job.job_degree}
职位-薪资待遇：{job.job_salary_description}
职位-职位详情：
{job.job_description}
</JobDetail>
"""
    evaluation = None

    async with evaluator_agent.run_stream(user_prompt) as result:
        async for message, is_last in result.stream_structured():
            try:
                evaluation = await result.validate_structured_output(
                    message,
                    allow_partial=not is_last,
                )

            except ValidationError:
                continue

    if evaluation is None:
        raise EvaluatorError("LLM cannot output well-formed result")

    return JobEvaluation(
        job_encrypt_id=job.job_encrypt_id,
        technology_match_score=evaluation.get("technology_match_score", Decimal(0)),
        technology_match_reason=evaluation.get("technology_match_reason", ""),
        project_experience_match_score=evaluation.get(
            "project_experience_match_score", Decimal(0)
        ),
        project_experience_match_reason=evaluation.get("project_experience_match_reason", ""),
        industry_experience_match_score=evaluation.get(
            "industry_experience_match_score", Decimal(0)
        ),
        industry_experience_match_reason=evaluation.get("industry_experience_match_reason", ""),
        level_match_score=evaluation.get("level_match_score", Decimal(0)),
        level_match_reason=evaluation.get("level_match_reason", ""),
        growth_potential_score=evaluation.get("growth_potential_score", Decimal(0)),
        growth_potential_reason=evaluation.get("growth_potential_reason", ""),
        technical_depth_potential_score=evaluation.get(
            "technical_depth_potential_score", Decimal(0)
        ),
        technical_depth_potential_reason=evaluation.get("technical_depth_potential_reason", ""),
    )


class EvaluatorError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
