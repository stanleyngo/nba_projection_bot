"""
dependencies.py — shared FastAPI dependencies and cross-cutting setup used
across api.py's routers: auth (get_current_user_id) and rate limiting
(limiter). Kept separate from any one router since every router needs both.
"""

import asyncio
import logging
import time
from os import getenv

import cachecontrol
import redis
import requests
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token
from slowapi import Limiter
from slowapi.util import get_remote_address

import nba_projection_bot.db as db

GOOGLE_CLIENT_ID = getenv("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID must be set.")

security = HTTPBearer()

# Built ONCE, module-level — verify_oauth2_token uses this to fetch
# Google's public signing certs, and a fresh Request() per call meant a
# network round-trip to Google on every authenticated request. Google's
# cert endpoint sends Cache-Control headers (~hours), which the
# cachecontrol-wrapped session honors, so certs are refetched only when
# they actually expire.
_google_auth_session = cachecontrol.CacheControl(requests.Session())
_google_auth_request = google_auth_requests.Request(session=_google_auth_session)

# Short-TTL cache of already-verified tokens -> internal user id, so a
# burst of requests from the same signed-in user (polling, rapid asks)
# doesn't re-verify the same JWT and re-query the users table every
# time. TTL is deliberately short: revocation/expiry still bites within
# a minute, and the token's own exp was already checked when it entered
# the cache. Evicted lazily; cleared outright if it somehow grows past
# the cap (bounded memory beats LRU bookkeeping at this scale).
_TOKEN_CACHE_TTL_SECONDS = 60
_TOKEN_CACHE_MAX_ENTRIES = 1024
_token_cache: dict[str, tuple[int, float]] = {}

# Rate limiting by client IP — shared across every router, and registered
# onto app.state in api.py's own setup.
limiter = Limiter(key_func=get_remote_address)


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
    token = credentials.credentials
    cached = _token_cache.get(token)
    if cached is not None:
        user_id, cached_at = cached
        if time.time() - cached_at < _TOKEN_CACHE_TTL_SECONDS:
            return user_id
        del _token_cache[token]
    try:
        payload = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            _google_auth_request,
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
    if len(_token_cache) >= _TOKEN_CACHE_MAX_ENTRIES:
        _token_cache.clear()
    _token_cache[token] = (user_id, time.time())
    return user_id


# Rate limiting Lua script for token bucket algorithm. This script is registered
# with Redis and executed atomically to ensure correct behavior in a distributed
# environment. It maintains the state of the token bucket in a Redis hash and
# calculates the number of tokens available based on the elapsed time since
# the last refill. If there are enough tokens, it decrements the token count
# and returns success; otherwise, it returns failure and the time until the
# next token is available. The script also sets an expiration on the bucket
# key to prevent stale data from lingering in Redis.

RATE_LIMIT_CAPACITY = 3
RATE_LIMIT_REFILL_RATE = 0.1
RATE_LIMIT_COST = 1


_TOKEN_BUCKET_SCRIPT_SOURCE = """
-- KEYS[1] = bucket key
-- ARGV[1] = max tokens (3)
-- ARGV[2] = refill rate (tokens per second)
-- ARGV[3] = cost (1, flat per request)
-- ARGV[4] = current timestamp (seconds) 

local last_refill = redis.call("HMGET", KEYS[1], "tokens", "last_refill")
local elapsed = tonumber(ARGV[4]) - tonumber(last_refill[2] or 0)
local tokens = math.min(tonumber(ARGV[1]), tonumber(last_refill[1] or 0) + elapsed * tonumber(ARGV[2]))
if tokens >= tonumber(ARGV[3]) then
    tokens = tokens - tonumber(ARGV[3])
    redis.call("HSET", KEYS[1], "tokens", tokens, "last_refill", ARGV[4])
    redis.call("EXPIRE", KEYS[1], 60)
    return {1, tokens}
else
    redis.call("HSET", KEYS[1], "tokens", tokens, "last_refill", ARGV[4])
    redis.call("EXPIRE", KEYS[1], 60)
    return {0, math.ceil((tonumber(ARGV[3]) - tokens) / tonumber(ARGV[2]))}
end
"""


def register_rate_limit_script(redis_client: redis.Redis) -> redis.commands.core.Script:
    """
    Registers the Lua script for token bucket rate limiting with the given
    Redis client. Returns a Script object that can be used to execute the
    script.
    """
    return redis_client.register_script(_TOKEN_BUCKET_SCRIPT_SOURCE)


async def enforce_rate_limit(request: Request, user_id: int = Depends(get_current_user_id)) -> None:
    """
    Enforces rate limiting for the given user_id using the token bucket
    algorithm implemented in Lua. Raises HTTPException with status code 429
    if the user has exceeded their rate limit.
    """
    script = request.app.state.rate_limit_script
    try:
        allowed, time_left = await asyncio.to_thread(
            script,
            keys=[f"rate_limit:{user_id}"],
            args=[RATE_LIMIT_CAPACITY, RATE_LIMIT_REFILL_RATE, RATE_LIMIT_COST, time.time()],
        )
    except redis.exceptions.RedisError:
        logging.exception("Rate limit check failed (Redis unreachable) — failing open")
        return
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Try again in {time_left} seconds.",
            headers={"Retry-After": str(time_left)},
        )
