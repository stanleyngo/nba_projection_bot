"""
api.py — Stage 5: expose the agent as an HTTP API.
"""

from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import nba_projection_bot.agent as agent
import nba_projection_bot.db as db

# Rate limiting by client IP — /ask triggers real, billed Anthropic API
# calls (possibly several, per the agent's tool-use loop), so this caps
# how fast any one client can spend your API budget.
limiter = Limiter(key_func=get_remote_address)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield
app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

class AskRequest(BaseModel):
    question: str = Field(max_length=500)
    conversation_id: int | None = None

class AskResponse(BaseModel):
    answer: str
    conversation_id: int
    # Projection card records for this turn (see agent.projection_record).
    # Empty when the turn made no projection (e.g. an injury-only question),
    # so older clients that ignore this field keep working unchanged.
    projections: list[dict] = []
    # News & analysis card records for this turn (see agent.news_record).
    # Empty when the turn pulled no news; older clients ignore it.
    news: list[dict] = []


# The React app is built into static/ by Vite (see frontend/vite.config.ts):
# static/index.html plus hashed static/assets/*. Both are generated at build
# time and git-ignored — run `npm --prefix frontend run build` to produce them.
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"

# Serve the hashed JS/CSS bundles. Guarded so the app still imports before a
# build has run (e.g. a fresh checkout, or the Python-only test run).
if ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
@limiter.limit("5/minute")
async def ask(request: Request, ask_request: AskRequest) -> AskResponse:
    try:
        answer, conversation_id, projections, news = await agent.run_agent(
            ask_request.question, ask_request.conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except anthropic.APIError:
        raise HTTPException(
            status_code=502,
            detail="The AI service is temporarily unavailable. Please try again later.",
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        )

    return AskResponse(answer=answer, conversation_id=conversation_id,
                       projections=projections, news=news)


if __name__ == "__main__":
    # Stage 5 checkpoint — this file isn't run directly like the others.
    # Instead, from src/, run:
    #     uvicorn nba_projection_bot.api:app --reload
    # Then open http://127.0.0.1:8000/docs to see the auto-generated API
    # docs and try the /ask endpoint interactively — same as you did with
    # the reference file's /notes endpoint, but this one triggers a real
    # (billed) call through run_agent().
    pass
