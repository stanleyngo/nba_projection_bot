"""
api.py — Stage 5: expose the agent as an HTTP API.
"""

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Query, Request
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

import nba_projection_bot.agent as agent
import nba_projection_bot.db as db

GOOGLE_CLIENT_ID = getenv("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID must be set.")

security = HTTPBearer()


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


# Rate limiting by client IP
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield


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


STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


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


if __name__ == "__main__":
    # This is the PRODUCTION entry point — what the Docker CMD actually
    # calls
    # For LOCAL dev, prefer running this instead, from src/:
    #     uvicorn nba_projection_bot.api:app --reload
    import uvicorn

    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
