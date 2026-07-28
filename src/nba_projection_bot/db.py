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

import datetime
from os import getenv

from dotenv import load_dotenv
from sqlalchemy import JSON, ForeignKey, Text, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

load_dotenv()
database_url = getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL must be set.")


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


engine = create_async_engine(database_url, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    # NOTE: create_all only creates tables that don't exist yet — it does
    # NOT add new columns to a table that's already
    # live in the database.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_or_create_user(auth_provider_id: str, email: str) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.auth_provider_id == auth_provider_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing.id
        new_user = User(auth_provider_id=auth_provider_id, email=email)
        session.add(new_user)
        await session.commit()
        await session.refresh(new_user)
        return new_user.id


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


async def load_history(conversation_id: int, user_id: int) -> list[dict]:
    async with async_session() as session:
        await _check_ownership(session, conversation_id, user_id)
        result = await session.execute(
            select(Message.role, Message.content, Message.projections, Message.news)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
        return [
            {
                "role": row.role,
                "content": row.content,
                "projections": row.projections or [],
                "news": row.news or [],
            }
            for row in result.fetchall()
        ]
