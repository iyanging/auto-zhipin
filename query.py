import argparse
import asyncio
from itertools import batched
import json
from typing import Any

from boss_zhipin import BossZhipin, Job
from job_eval import Result, spawn_workflow
from utils import async_batched


async def main(args: argparse.Namespace) -> None:
    filter_tags = set(t.strip() for t in args.filter_tags.split(","))
    ratings = set(r.strip() for r in args.ratings.split(","))

    with open(args.resume, "r", encoding="utf-8") as f:
        resume = f.read()

    if args.blacklist:
        with open(args.blacklist, "r") as f:
            blacklist = set(company.strip() for company in f.readlines())
    else:
        blacklist = None

    job_eval_list: list[dict[str, Any]] = []
    zhipin = BossZhipin()

    async for jobs in async_batched(
        zhipin.query_jobs(
            args.query,
            args.city,
            args.scroll_n,
            filter_tags,
            blacklist,
        ),
        8,
    ):
        for job, result in await asyncio.gather(
            *[do_workflow(resume, job) for job in jobs]
        ):
            job_eval_list.append(job.model_dump())
            save(job_eval_list, args.output)

            if result.rating in ratings:
                await job.favor()


async def do_workflow(resume: str, job: Job) -> tuple[Job, Result]:
    workflow = spawn_workflow()
    result = await workflow(resume, job.description())

    return (job, result)


def save(job_eval_list: list[dict[str, Any]], output: str) -> None:
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(job_eval_list, f)


if __name__ == "__main__":
    cliparser = argparse.ArgumentParser(description="查询匹配的岗位。")
    cliparser.add_argument(
        "--resume",
        help="简历文件路径 (目前只支持文本文件，推荐使用Markdown)",
        type=str,
        required=True,
    )
    cliparser.add_argument("-q", "--query", help="查询关键字", type=str, default="")
    cliparser.add_argument(
        "--city",
        help="BOSS直聘城市代码 (默认: 100010000)",
        type=str,
        default="100010000",
    )
    cliparser.add_argument(
        "-n", "--scroll_n", help="最大滚动次数 (默认: 8)", type=int, default=8
    )
    cliparser.add_argument(
        "--filter_tags",
        help="需要过滤的岗位标签 (默认: 派遣,猎头)",
        type=str,
        default="派遣,猎头",
    )
    cliparser.add_argument(
        "--ratings",
        help="可接受的岗位评级 (默认: EXCELLENT,GOOD)",
        type=str,
        default="EXCELLENT,GOOD",
    )
    cliparser.add_argument(
        "--blacklist", help="公司黑名单文件路径 (每行一个公司名称)", type=str
    )
    cliparser.add_argument(
        "-O",
        "--output",
        help="岗位列表JSON文件输出路径 (默认: jobs.json)",
        type=str,
        default="jobs.json",
    )
    args, _ = cliparser.parse_known_args()

    asyncio.run(main(args))
