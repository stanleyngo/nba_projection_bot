"""
db.py

This module's job: give run_agent() a durable place to read and write
conversation history, so a conversation survives across separate HTTP
requests (and server restarts) instead of living only inside one
run_agent() call's local `messages` list.

Backing store: a small hosted Postgres (e.g. Neon or Supabase's free
tier), accessed asynchronously via SQLAlchemy's async engine + the
asyncpg driver

Deliberate scope choice: only the final text of each turn is stored here
("user asked X" / "assistant answered Y") — NOT the full Anthropic
content-block structure (tool_use/tool_result blocks) that run_agent's
internal loop generates while answering a single question. Two reasons:
  1. It sidesteps SDK-object serialization entirely — every value that
     touches this table is already a plain string, never a Pydantic
     content-block object that needs converting first.
  2. On the NEXT question, the model doesn't need a replay of exactly
     which tools it called last time — just the substance of what was
     asked and answered, to stay coherent as a conversation.
"""

import asyncio
import datetime
import enum
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import JSON, ForeignKey, Text, UniqueConstraint, func, or_, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()


class JobStatus(str, enum.Enum):
    queued = "queued"
    fetching = "fetching"
    simulating = "simulating"
    summarizing = "summarizing"
    done = "done"
    failed = "failed"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    auth_provider_id: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str]
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    # Nullable — a brand-new conversation has no title yet; it gets filled
    # in once (see agent.py) right after the first exchange completes, via
    # a short LLM-generated summary. NULL just means "not generated yet",
    # not an error (e.g. if title generation happened to fail).
    title: Mapped[str | None] = mapped_column(default=None)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    # The projection/news card data a live turn showed (see agent.py's
    # projection_record/news_record) — NULL for user messages, and for
    # assistant messages that surfaced no cards. Kept separate from
    # `content` so a past conversation can replay the same cards it
    # originally showed, not just the plain-text answer.
    projections: Mapped[list | None] = mapped_column(JSON, default=None)
    news: Mapped[list | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())


class DeepAnalysisJob(Base):
    __tablename__ = "deep_analysis_jobs"
    # Idempotency keys are scoped PER USER, not globally:
    # (Applied to the live table by Alembic migration 002.)
    __table_args__ = (
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_deep_analysis_jobs_user_id_idempotency_key"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    player_name: Mapped[str]
    # Nullable only because rows created before this column existed have no
    # value for it — every row created going forward always has one
    # (resolved once, at submission time, and reused by the produce-retry
    # loop so it never needs to re-resolve the player name).
    player_id: Mapped[int | None] = mapped_column(default=None)
    status: Mapped[JobStatus] = mapped_column(default=JobStatus.queued.value)
    result: Mapped[str | None] = mapped_column(Text, default=None)
    error: Mapped[str | None] = mapped_column(default=None)
    retry_count: Mapped[int] = mapped_column(default=0)
    # False until the job's Kafka message is CONFIRMED delivered (set True
    # only after flush() reports success — never optimistically at claim
    # time). Distinct from `status`: a job can be `queued` and NOT YET
    # produced (never reached Kafka, waiting on the retry loop) or
    # `queued` and already produced (delivered fine, just waiting for a
    # worker to consume it) — `status` alone can't tell these apart.
    produced: Mapped[bool] = mapped_column(default=False)
    # The produce-claim LEASE: set to now() when a caller claims the right
    # to produce this job (see claim_job_for_producing), cleared on a
    # confirmed failure. A claim whose holder crashed mid-produce simply
    # goes stale — once it's older than the lease window, the retry loop
    # can reclaim the job instead of it being lost forever.
    produce_claimed_at: Mapped[datetime.datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


# Engine/sessionmaker are NOT built at import time
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_database_url: str | None = None


def configure(database_url: str) -> None:
    """Build this process's engine + sessionmaker. Idempotent — a second
    call is a no-op, so tests/entry points can't double-construct."""
    global _engine, _session_factory, _database_url
    if _engine is not None:
        return
    _database_url = database_url
    _engine = create_async_engine(database_url, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


def async_session() -> AsyncSession:
    """Open a session — same call shape as the old module-level
    sessionmaker (`async with db.async_session() as session:`)."""
    if _session_factory is None:
        raise RuntimeError("db.configure(DATABASE_URL) must be called before using the database.")
    return _session_factory()


async def dispose() -> None:
    """Close the engine's connection pool (lifespan teardown)."""
    if _engine is not None:
        await _engine.dispose()


async def init_db() -> None:
    """
    Bring the database schema up to date by running Alembic migrations
    (replaces the old create_all call, which could only CREATE missing
    tables — it silently could not alter existing ones, which this
    project got burned by more than once).
    """
    if _database_url is None:
        raise RuntimeError("db.configure(DATABASE_URL) must be called before init_db().")
    await asyncio.to_thread(_run_migrations, _database_url)


def _run_migrations(database_url: str) -> None:
    # Configured programmatically (script_location resolved relative to
    # this package) so the migrations run identically from the Docker
    # image, local dev, and the alembic CLI. Runs inside to_thread: the
    # async env.py uses asyncio.run(), which needs a thread that has no
    # event loop of its own.
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


async def get_or_create_user(auth_provider_id: str, email: str) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.auth_provider_id == auth_provider_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing.id
        # Two concurrent first-ever requests from the same new user can
        # both reach here (both SELECTs saw nothing) — a plain INSERT
        # would race, and the loser would blow up on the unique
        # constraint. Same atomic upsert pattern as request_deep_analysis:
        # whoever loses the race just reads the winner's row instead.
        stmt = (
            pg_insert(User)
            .values(auth_provider_id=auth_provider_id, email=email)
            .on_conflict_do_nothing(index_elements=["auth_provider_id"])
            .returning(User.id)
        )
        new_id = (await session.execute(stmt)).scalar_one_or_none()
        await session.commit()
        if new_id is not None:
            return new_id
        winner_id = await session.scalar(
            select(User.id).where(User.auth_provider_id == auth_provider_id)
        )
        if winner_id is None:
            raise RuntimeError("User upsert failed unexpectedly.")
        return winner_id


async def create_conversation(user_id: int) -> int:
    async with async_session() as session:
        new_conversation = Conversation(user_id=user_id)
        session.add(new_conversation)
        await session.commit()
        await session.refresh(new_conversation)
        return new_conversation.id


async def list_conversations(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Conversation.id, Conversation.title, Conversation.created_at)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            {"id": row.id, "title": row.title, "created_at": row.created_at}
            for row in result.fetchall()
        ]


async def set_conversation_title(conversation_id: int, title: str) -> None:
    """
    Set a conversation's title (called once, right after its first
    exchange — see agent.py). No ownership check here: this is only ever
    called internally, right after run_agent itself already created or
    verified ownership of conversation_id for this same request — unlike
    append_message/load_history, it's never exposed as something a
    request handler calls directly with a caller-supplied conversation_id.
    """
    async with async_session() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} does not exist.")
        conversation.title = title
        await session.commit()


# 7. async def append_message(conversation_id: int, role: str, content: str) -> None
#    Open a session, insert a new Message row, commit.


async def _check_ownership(session, conversation_id: int, user_id: int) -> None:
    """
    Raise PermissionError unless `conversation_id` belongs to `user_id`.

    Deliberately does NOT distinguish "conversation doesn't exist" from
    "conversation exists but belongs to someone else" — both cases raise
    the same generic error, so this can't be used to probe which
    conversation ids exist at all.
    """
    owner = await session.scalar(
        select(Conversation.user_id).where(Conversation.id == conversation_id)
    )
    if owner != user_id:
        raise PermissionError("This conversation does not belong to this user.")


async def append_message(
    conversation_id: int,
    user_id: int,
    role: str,
    content: str,
    projections: list[dict] | None = None,
    news: list[dict] | None = None,
) -> None:
    async with async_session() as session:
        await _check_ownership(session, conversation_id, user_id)
        new_message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            projections=projections,
            news=news,
        )
        session.add(new_message)
        await session.commit()


async def load_history(
    conversation_id: int, user_id: int, max_messages: int | None = None
) -> list[dict]:
    """
    A conversation's messages, oldest-first. `max_messages` returns only
    the most RECENT n (still oldest-first) — the agent path caps what it
    sends to Anthropic (token cost grows with every turn otherwise, until
    old conversations overflow the context window entirely), while the UI
    history endpoint passes None and shows everything.
    """
    async with async_session() as session:
        await _check_ownership(session, conversation_id, user_id)
        stmt = (
            select(Message.role, Message.content, Message.projections, Message.news)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
        )
        if max_messages is not None:
            stmt = stmt.limit(max_messages)
        result = await session.execute(stmt)
        rows = list(result.fetchall())[::-1]  # fetched newest-first; restore oldest-first
        return [
            {
                "role": row.role,
                "content": row.content,
                "projections": row.projections or [],
                "news": row.news or [],
            }
            for row in rows
        ]


async def list_deep_analysis_jobs(user_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(
                DeepAnalysisJob.id,
                DeepAnalysisJob.player_name,
                DeepAnalysisJob.status,
                DeepAnalysisJob.produced,
                DeepAnalysisJob.created_at,
            )
            .where(DeepAnalysisJob.user_id == user_id)
            .order_by(DeepAnalysisJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            {
                "id": row.id,
                "player_name": row.player_name,
                "status": row.status,
                "produced": row.produced,
                "created_at": row.created_at,
            }
            for row in result.fetchall()
        ]


async def get_deep_analysis_job(job_id: int, user_id: int) -> dict:
    async with async_session() as session:
        job = await session.get(DeepAnalysisJob, job_id)
        if job is None or job.user_id != user_id:
            raise PermissionError("This job does not belong to this user.")
        return {
            "status": job.status,
            "result": job.result,
            "error": job.error,
            "produced": job.produced,
            "created_at": job.created_at,
        }


# How stale a produce claim must be before another caller may steal it.
# A produce attempt takes seconds (flush timeout is 3s), so 5 minutes
# only ever matters when a claim holder CRASHED mid-produce — the case
# the lease exists for. Server-side interval so the comparison uses the
# database's clock, not whichever app server's.
_PRODUCE_LEASE = text("interval '5 minutes'")


async def claim_job_for_producing(job_id: int) -> bool:
    """
    Atomically claim the right to produce one job's Kafka message — returns
    True only if THIS call took the claim.

    Claiming is a LEASE (produce_claimed_at = now()), not a permanent flag:
      - a rival caller inside the lease window loses the claim (returns
        False) and must not produce — that's the duplicate-message guard
        between the request handler and the retry loop;
      - a claim older than the lease is presumed dead (its holder crashed
        between claiming and producing — no exception ever fired, so
        release was never called) and CAN be re-claimed. Without this,
        a crash in that window left the job unproducible forever.
    """
    async with async_session() as session:
        result = await session.execute(
            sql_update(DeepAnalysisJob)
            .where(
                DeepAnalysisJob.id == job_id,
                DeepAnalysisJob.produced.is_(False),
                or_(
                    DeepAnalysisJob.produce_claimed_at.is_(None),
                    DeepAnalysisJob.produce_claimed_at < func.now() - _PRODUCE_LEASE,
                ),
            )
            .values(produce_claimed_at=func.now())
            .returning(DeepAnalysisJob.id)
        )
        claimed_id = result.scalar_one_or_none()
        await session.commit()
        return claimed_id is not None


async def mark_job_produced(job_id: int) -> None:
    """Record confirmed Kafka delivery — only ever called after flush()
    reported success, never optimistically."""
    async with async_session() as session:
        await session.execute(
            sql_update(DeepAnalysisJob).where(DeepAnalysisJob.id == job_id).values(produced=True)
        )
        await session.commit()


async def release_job_for_producing(job_id: int) -> None:
    """Give up a claim after a CONFIRMED produce failure, so the retry
    loop can pick the job up again immediately (no lease wait)."""
    async with async_session() as session:
        await session.execute(
            sql_update(DeepAnalysisJob)
            .where(DeepAnalysisJob.id == job_id)
            .values(produce_claimed_at=None)
        )
        await session.commit()


# How long a job may sit in an in-flight status without a single commit
# before it's presumed abandoned (its worker died mid-job). Every
# set_status commit refreshes updated_at (onupdate), so a LIVE worker's
# job never goes this long without a heartbeat — the longest real gap is
# one fetching/summarizing phase, a few minutes at worst.
_PROCESSING_LEASE = text("interval '10 minutes'")

_IN_FLIGHT_STATUSES = (JobStatus.fetching, JobStatus.simulating, JobStatus.summarizing)


async def claim_job_for_processing(job_id: int) -> bool:
    """
    Atomically claim a job for processing — returns True only if THIS
    worker took it. The claim is the queued -> fetching transition itself
    (compare-and-set on status), so two workers handed the same message
    (consumer-group rebalance redelivering an uncommitted offset) can't
    both walk the job through the state machine and double-spend the
    Anthropic calls.

    """
    async with async_session() as session:
        result = await session.execute(
            sql_update(DeepAnalysisJob)
            .where(
                DeepAnalysisJob.id == job_id,
                or_(
                    DeepAnalysisJob.status == JobStatus.queued,
                    DeepAnalysisJob.status.in_(_IN_FLIGHT_STATUSES)
                    & (DeepAnalysisJob.updated_at < func.now() - _PROCESSING_LEASE),
                ),
            )
            .values(status=JobStatus.fetching, updated_at=func.now())
            .returning(DeepAnalysisJob.id)
        )
        claimed_id = result.scalar_one_or_none()
        await session.commit()
        return claimed_id is not None


async def list_unproduced_jobs() -> list[dict]:
    """Every job whose Kafka message has never been confirmed delivered."""
    async with async_session() as session:
        result = await session.execute(
            select(
                DeepAnalysisJob.id, DeepAnalysisJob.player_name, DeepAnalysisJob.player_id
            ).where(DeepAnalysisJob.produced.is_(False))
        )
        return [
            {"id": row.id, "player_name": row.player_name, "player_id": row.player_id}
            for row in result.fetchall()
        ]
