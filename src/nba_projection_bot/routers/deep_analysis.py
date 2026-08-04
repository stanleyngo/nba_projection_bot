"""
routers/deep_analysis.py — the async deep-analysis job queue: submitting a
job, listing history, and polling one job's status.
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

import nba_projection_bot.db as db
import nba_projection_bot.kafka_producer as kafka_producer
from nba_projection_bot import data
from nba_projection_bot.dependencies import get_current_user_id, limiter
from nba_projection_bot.schemas import (
    DeepAnalysisJobSummary,
    DeepAnalysisRequest,
    DeepAnalysisResponse,
)

router = APIRouter()


@router.get("/deep-analysis", response_model=list[DeepAnalysisJobSummary])
@limiter.limit("30/minute")  # polled once per tick by the frontend, regardless of job count
async def get_deep_analysis_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user_id),
) -> list[dict]:
    try:
        jobs = await db.list_deep_analysis_jobs(user_id, limit=limit, offset=offset)
    except Exception as e:
        logging.exception("Unexpected error in GET /deep-analysis")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return jobs


@router.post("/deep-analysis")
@limiter.limit("5/minute")
async def request_deep_analysis(
    request: Request,
    body: DeepAnalysisRequest,
    idempotency_key: str = Header(...),
    user_id: int = Depends(get_current_user_id),
):
    try:
        # Resolve the player BEFORE ever writing a row — an ambiguous or
        # unknown name should fail outright, not leave a stuck, permanently
        # "queued" entry sitting in the user's report history.
        player_id = data.resolve_player_id(body.player_name)
        if player_id is None:
            raise HTTPException(status_code=400, detail=f"Player '{body.player_name}' not found.")

        async with db.async_session() as session:
            stmt = (
                pg_insert(db.DeepAnalysisJob)
                .values(
                    idempotency_key=idempotency_key,
                    user_id=user_id,
                    player_name=body.player_name,
                    player_id=player_id,
                    status=db.JobStatus.queued.value,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "idempotency_key"])
                .returning(db.DeepAnalysisJob.id)
            )
            result = await session.execute(stmt)
            new_id = result.scalar_one_or_none()
            await session.commit()

            if new_id is not None:
                job_id = new_id
                # Claim before producing — the background produce-retry loop
                # (see kafka_producer.retry_unproduced_jobs) could otherwise
                # race to produce this same job at the same time, if it
                # happens to poll in the moment between this row being
                # inserted and this request's own produce attempt finishing.
                claimed = await db.claim_job_for_producing(job_id)
                if claimed:
                    try:
                        await kafka_producer.produce_job_event(job_id, player_id=player_id)
                        await db.mark_job_produced(job_id)
                    except Exception:
                        # Not fatal to the request: the job row exists and
                        # correctly reflects "not yet produced" once
                        # released — the background retry loop picks it up
                        # and keeps trying once Kafka is reachable again.
                        # The user doesn't need to know this happened or
                        # resubmit anything (this is common here, since the
                        # free-tier Kafka service sleeps after inactivity).
                        logging.exception(
                            f"Failed to produce Kafka event for deep-analysis job {job_id}; "
                            "will be retried automatically"
                        )
                        await db.release_job_for_producing(job_id)
            else:
                existing = await session.scalar(
                    select(db.DeepAnalysisJob).where(
                        db.DeepAnalysisJob.idempotency_key == idempotency_key,
                        # Scoped to this user — with per-user key
                        # uniqueness, the conflict can only ever be with
                        # this user's own earlier submission.
                        db.DeepAnalysisJob.user_id == user_id,
                    )
                )
                if existing is None:
                    raise HTTPException(status_code=500, detail="Job lookup failed unexpectedly.")
                job_id = existing.id
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logging.exception("Unexpected error in /deep-analysis")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e

    return {"job_id": job_id}


@router.get("/deep-analysis/{job_id}")
@limiter.limit("30/minute")  # polled by the frontend every few seconds while a job is in flight
async def get_deep_analysis(
    request: Request, job_id: int, user_id: int = Depends(get_current_user_id)
):
    try:
        job = await db.get_deep_analysis_job(job_id, user_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="You don't have access to this job.") from e
    except Exception as e:
        logging.exception(f"Unexpected error in GET /deep-analysis/{job_id}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return DeepAnalysisResponse(**job)
