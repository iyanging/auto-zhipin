import logging

from auto_zhipin.boss_zhipin import Job
from auto_zhipin.db import JobEvaluation

logger = logging.getLogger(__name__)


async def evaluate_job(job: Job) -> JobEvaluation:
    logger.info("Evaluating job %s", job)

    # TODO: evaluate

    return JobEvaluation(
        job_encrypt_id=job.job_encrypt_id,
    )
