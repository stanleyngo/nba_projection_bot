"""
api.py — Stage 5: expose the agent as an HTTP API.

Thin orchestration layer only: builds the FastAPI app, wires up shared
startup resources (lifespan), middleware, and mounts the actual route
handlers, which live in routers/. See dependencies.py (auth, rate
limiting), kafka_producer.py (publishing deep-analysis job events), and
schemas.py (request/response models) for everything this file delegates to.
"""

import asyncio
from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import nba_projection_bot.db as db
import nba_projection_bot.kafka_producer as kafka_producer
import nba_projection_bot.tools as tools
from nba_projection_bot import data
from nba_projection_bot.dependencies import limiter, register_rate_limit_script
from nba_projection_bot.routers import chat, deep_analysis

REDIS_URL = getenv("REDIS_URL")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL must be set.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    assert REDIS_URL is not None  # narrowed above; mypy can't see across function scopes
    # Built once here, not as a data.py module-level singleton — see
    # DECISIONS.md for the reasoning (testability, centralized
    # construction/teardown, no import-order side effects). The raw client
    # is built here, top-level, since it's shared infrastructure — data.py
    # and dependencies.py each just register their own scripts against it,
    # rather than one of them owning client construction for both. Stored
    # on app.state so any route/dependency that needs it can reach it via
    # request.app.state.redis_resources, and bound into the Redis-dependent
    # tools once here too, so run_agent's tool-dispatch chain never has to
    # know it exists.
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    app.state.redis_resources = data.build_redis_resources(redis_client)
    app.state.rate_limit_script = register_rate_limit_script(redis_client)
    tools.bind_redis_resources(app.state.redis_resources)
    retry_task = asyncio.create_task(kafka_producer.retry_unproduced_jobs())
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

app.include_router(chat.router)
app.include_router(deep_analysis.router)

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


if __name__ == "__main__":
    # This is the PRODUCTION entry point — what the Docker CMD actually
    # calls
    # For LOCAL dev, prefer running this instead, from src/:
    #     uvicorn nba_projection_bot.api:app --reload
    import uvicorn

    port = int(getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
