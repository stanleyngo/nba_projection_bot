"""
db.py — Stage 6: persistent conversation storage.

This module's job: give run_agent() a durable place to read and write
conversation history, so a conversation survives across separate HTTP
requests (and server restarts) instead of living only inside one
run_agent() call's local `messages` list.

Backing store: a small hosted Postgres (e.g. Neon or Supabase's free
tier), accessed asynchronously via SQLAlchemy's async engine + the
asyncpg driver — consistent with the rest of this project being async
end-to-end.

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

from os import getenv
from dotenv import load_dotenv
import datetime
from sqlalchemy import ForeignKey, Text, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

load_dotenv()
database_url = getenv("DATABASE_URL")

class Base(DeclarativeBase):
   pass

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    role: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(server_default=func.now())

engine = create_async_engine(database_url)
async_session = async_sessionmaker(engine, expire_on_commit=False)

# 5. async def init_db() -> None
#    Creates the tables if they don't already exist.

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# 6. async def create_conversation() -> int
#    Open a session, insert a new Conversation row, commit, return its id.

async def create_conversation() -> int:
    async with async_session() as session:
        new_conversation = Conversation()
        session.add(new_conversation)
        await session.commit()
        await session.refresh(new_conversation)
        return new_conversation.id

# 7. async def append_message(conversation_id: int, role: str, content: str) -> None
#    Open a session, insert a new Message row, commit.

async def append_message(conversation_id: int, role: str, content: str) -> None:
    async with async_session() as session:
        new_message = Message(conversation_id = conversation_id, role=role, content=content)
        session.add(new_message)
        await session.commit()

# 8. async def load_history(conversation_id: int) -> list[dict]
#    Query all Message rows for this conversation_id ordered by id

async def load_history(conversation_id: int) -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )
        return [{"role": row.role, "content": row.content} for row in result.fetchall()]
