"""
api.py — Stage 5: expose the agent as an HTTP API.
"""

import asyncio
import datetime
import logging
import json
from confluent_kafka import Producer
from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from nba_projection_bot import data
import nba_projection_bot.agent as agent
import nba_projection_bot.db as db

GOOGLE_CLIENT_ID = getenv("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID must be set.")

KAFKA_BOOTSTRAP_SERVERS = getenv("KAFKA_SERVICE_URI")
KAFKA_USERNAME = getenv("KAFKA_USERNAME")
KAFKA_PASSWORD = getenv("KAFKA_PASSWORD")
KAFKA_CA_CERT = getenv("KAFKA_CA_CERT")  # PEM content, not a file path
if not KAFKA_BOOTSTRAP_SERVERS or not KAFKA_USERNAME or not KAFKA_PASSWORD or not KAFKA_CA_CERT:
    raise RuntimeError(
        "KAFKA_SERVICE_URI, KAFKA_USERNAME, KAFKA_PASSWORD, and KAFKA_CA_CERT must all be set."
    )

# ssl.ca.location needs an actual file path — write the PEM content out
# once at import time rather than requiring a file to already exist on
# disk in every environment this runs in (local, Render, etc.).
_ca_cert_path = Path(__file__).parent / "kafka_ca.pem"
_ca_cert_path.write_text(KAFKA_CA_CERT)

security = HTTPBearer()

producer = Producer(
    {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": KAFKA_USERNAME,
        "sasl.password": KAFKA_PASSWORD,
        "ssl.ca.location": str(_ca_cert_path),
    }
)

# Rate limiting by client IP
limiter = Limiter(key_func=get_remote_address)


PRODUCE_RETRY_INTERVAL_SECONDS = 300

async def _retry_unproduced_jobs() -> None:
    """
    Background task, runs for the process's lifetime: periodically retries
    publishing any job whose Kafka message was never confirmed delivered
    
    """
    while True:
        await asyncio.sleep(PRODUCE_RETRY_INTERVAL_SECONDS)
        try:
            jobs = await db.list_unproduced_jobs()
            for job in jobs:
                if job["player_id"] is None:
                    # Only possible for a row created before this column
                    # existed — nothing to retry it with.
                    continue
                claimed = await db.claim_job_for_producing(job["id"])
                if not claimed:
                    continue  # the original request handler already got to it
                try:
                    await produce_job_event(job["id"], player_id=job["player_id"])
                    logging.info(f"Produce-retry succeeded for deep-analysis job {job['id']}")
                except Exception:
                    logging.exception(f"Produce-retry failed for job {job['id']}, will retry again")
                    await db.release_job_for_producing(job["id"])
        except Exception:
            logging.exception("Unexpected error in produce-retry loop")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    retry_task = asyncio.create_task(_retry_unproduced_jobs())
    yield
    retry_task.cancel()

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
# slowapi's documented pattern — Starlette's stub can't express a handler
# narrowed to one specific exception subclass
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

allowed_origins = getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class AskRequest(BaseModel):
    question: str = Field(max_length=500)
    conversation_id: int | None = None


class AskResponse(BaseModel):
    answer: str
    conversation_id: int
    projections: list[dict] = []
    news: list[dict] = []


class ConversationSummary(BaseModel):
    id: int
    title: str | None
    created_at: datetime.datetime


class ConversationMessage(BaseModel):
    role: str
    content: str
    projections: list[dict] = []
    news: list[dict] = []


class ConversationHistoryResponse(BaseModel):
    messages: list[ConversationMessage]


class DeepAnalysisRequest(BaseModel):
    player_name: str


class DeepAnalysisResponse(BaseModel):
    status: str
    result: str | None
    error: str | None
    produced: bool
    created_at: datetime.datetime


class DeepAnalysisJobSummary(BaseModel):
    id: int
    player_name: str
    status: str
    produced: bool
    created_at: datetime.datetime


STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


def _delivery_report(err, msg):
    if err is not None:
        logging.error(f"Delivery failed for message {msg.key()}: {err}")
    else:
        logging.info(
            f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}"
        )


async def produce_job_event(job_id: int, player_id: int) -> None:
    """
    Publish "process this job" to deep-analysis-jobs, keyed by player_id.

    Raises RuntimeError if the broker never confirms delivery within the
    flush timeout, or confirms a failure — the caller must not treat this
    job as successfully queued if we can't actually confirm that.
    """
    delivery_errors: list[str] = []

    def _on_delivery(err, msg):
        _delivery_report(err, msg)
        if err is not None:
            delivery_errors.append(str(err))

    def _produce():
        producer.produce(
            topic="deep-analysis-jobs",
            key=str(player_id).encode("utf-8"),
            value=json.dumps({"job_id": job_id}).encode("utf-8"),
            on_delivery=_on_delivery,
        )
        pending = producer.flush(timeout=10)
        if pending > 0:
            raise RuntimeError(
                f"Timed out waiting for Kafka to confirm delivery of job {job_id} "
                f"({pending} message(s) still pending)."
            )
        if delivery_errors:
            raise RuntimeError(delivery_errors[0])

    await asyncio.to_thread(_produce)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> int:
    """
    The auth dependency every authenticated endpoint uses. Verifies the
    bearer token against Google's own public keys (and that it was
    actually issued for THIS app, via GOOGLE_CLIENT_ID) before trusting
    anything in it — user_id must never come from anywhere else (e.g.
    never add a user_id field to AskRequest), since that would let any
    client simply claim to be any user.
    """
    try:
        payload = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            credentials.credentials,
            google_auth_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        user_id = await db.get_or_create_user(payload["sub"], payload["email"])
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token.") from e
    except Exception as e:
        logging.exception("Token verification failed unexpectedly")
        raise HTTPException(
            status_code=503, detail=("Unable to verify sign-in right now. Please try again later.")
        ) from e
    return user_id


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


@app.get("/conversations", response_model=list[ConversationSummary])
@limiter.limit("5/minute")
async def get_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user_id),
) -> list[dict]:
    try:
        conversations = await db.list_conversations(user_id, limit=limit, offset=offset)
    except Exception as e:
        logging.exception("Unexpected error in GET /conversations")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return conversations


@app.get("/conversations/{conversation_id}", response_model=ConversationHistoryResponse)
@limiter.limit("5/minute")
async def get_conversation(
    request: Request,
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
) -> ConversationHistoryResponse:
    try:
        messages = await db.load_history(conversation_id, user_id)
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail="You don't have access to this conversation."
        ) from e
    except Exception as e:
        logging.exception(f"Unexpected error in GET /conversations/{conversation_id}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return ConversationHistoryResponse(messages=[ConversationMessage(**m) for m in messages])


@app.post("/ask", response_model=AskResponse)
@limiter.limit("5/minute")
async def ask(
    request: Request,
    ask_request: AskRequest,
    user_id: int = Depends(get_current_user_id),
) -> AskResponse:
    try:
        answer, conversation_id, projections, news = await agent.run_agent(
            ask_request.question, user_id, ask_request.conversation_id
        )
    except PermissionError as e:
        # Deliberately generic: don't confirm/deny whether conversation_id
        # exists at all
        raise HTTPException(
            status_code=403, detail="You don't have access to this conversation."
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except anthropic.APIError as e:
        logging.exception("Anthropic API error in /ask")
        raise HTTPException(
            status_code=502,
            detail="The AI service is temporarily unavailable. Please try again later.",
        ) from e
    except Exception as e:
        logging.exception("Unexpected error in /ask")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e

    return AskResponse(
        answer=answer, conversation_id=conversation_id, projections=projections, news=news
    )


@app.get("/deep-analysis", response_model=list[DeepAnalysisJobSummary])
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


@app.post("/deep-analysis")
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
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(db.DeepAnalysisJob.id)
            )
            result = await session.execute(stmt)
            new_id = result.scalar_one_or_none()
            await session.commit()

            if new_id is not None:
                job_id = new_id
                # Claim before producing — the background produce-retry loop
                # (see _retry_unproduced_jobs) could otherwise race to
                # produce this same job at the same time, if it happens to
                # poll in the moment between this row being inserted and
                # this request's own produce attempt finishing.
                claimed = await db.claim_job_for_producing(job_id)
                if claimed:
                    try:
                        await produce_job_event(job_id, player_id=player_id)
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
                        db.DeepAnalysisJob.idempotency_key == idempotency_key
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


@app.get("/deep-analysis/{job_id}")
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


if __name__ == "__main__":
    # This is the PRODUCTION entry point — what the Docker CMD actually
    # calls
    # For LOCAL dev, prefer running this instead, from src/:
    #     uvicorn nba_projection_bot.api:app --reload
    import uvicorn

    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
