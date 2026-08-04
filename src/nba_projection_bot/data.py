"""
data.py

This module's job: given a player name and a stat, return that player's values
over their most recent N games.

NOTE: nba_api calls stats.nba.com, which can rate-limit, time out, or change
its response shape. If something breaks, verify function names against the
current nba_api docs.
"""

import io
import json
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from os import getenv
from zoneinfo import ZoneInfo

import pandas as pd
import redis
import requests
from dotenv import load_dotenv
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.library.parameters import SeasonTypePlayoffs
from nba_api.stats.static import players

load_dotenv()

STAT_COLUMNS = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "steals": "STL",
    "blocks": "BLK",
    "threes": "FG3M",
}

# Upper bound on n_games — without this, a large value means many
# sequential nba_api calls (slow, risks getting rate-limited by
# stats.nba.com) and a large tool result sent back to the LLM (inflates
# input-token cost). 200 comfortably covers a couple of full seasons.
MAX_N_GAMES = 200

MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0

# stats.nba.com silently blocks/times out requests from most cloud provider
# IP ranges (Render included) while working fine from a residential IP —
# routing through a residential/rotating proxy works around this. Optional:
# None (the default, if unset) means "call stats.nba.com directly", which is
# what local dev wants since a home IP isn't blocked. Format is a standard
# proxy URL, e.g. "http://user:pass@host:port".
NBA_API_PROXY = getenv("NBA_API_PROXY")

def current_season() -> str:
    """
    The season string for today's date, e.g. "2025-26". Computed per call,
    not at import — a long-lived process that crosses the Nov 1 season
    rollover must not keep serving last season forever.
    """
    today = date.today()
    start_year = today.year if today.month >= 11 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"

LOCK_TTL_MS = 120000
POLL_INTERVAL_SECONDS = 0.2
# How long a WAITER polls for the lock holder's cache write before giving
# up. Deliberately much shorter than LOCK_TTL_MS (which bounds the
# HOLDER's fetch): each waiter pins a thread-pool thread for its whole
# wait (the poll loop runs inside asyncio.to_thread), and a wait longer
# than the platform's gateway timeout (~100s on Render) would just get
# the request killed upstream anyway. A holder's fetch normally completes
# in seconds; 30s covers the slow tail, and past that the waiter's
# "still loading, try again" error beats silently eating a thread.
MAX_WAIT_SECONDS = 30

_RELEASE_LOCK_SCRIPT_SOURCE = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
"""


@dataclass
class RedisResources:
    client: redis.Redis
    release_lock_script: redis.commands.core.Script


def build_redis_resources(client: redis.Redis) -> RedisResources:
    """
    Register this module's own Lua script against an already-built client
    — called once, at process startup (see api.py's lifespan / worker.py's
    module-level setup), not per-call. The client itself is built by each
    process's own entry point, not here: it's shared infrastructure other
    modules (e.g. dependencies.py's rate limiter) also register their own
    scripts against, so construction shouldn't be bundled inside one
    module's builder. Every function below that needs Redis receives the
    resulting RedisResources instead of reaching for a module-level global:
    this module has no hidden dependency on Redis existing until something
    explicitly hands it one, which makes it easy to substitute a fake for
    tests, and means construction/teardown lives in one deliberate place
    instead of being an implicit side effect of import order.
    """
    release_lock_script = client.register_script(_RELEASE_LOCK_SCRIPT_SOURCE)
    return RedisResources(client=client, release_lock_script=release_lock_script)


def _cache_key(player_id: int, season: str, season_type: str) -> str:
    return f"nba:gamelog:{player_id}:{season}:{season_type}"


def _release_lock(redis_resources: RedisResources, lock_key: str, token: str) -> bool:
    return redis_resources.release_lock_script(keys=[lock_key], args=[token])


# expiring cache at 2am EST because all games are basically guaranteed
# to be finished by then
def _seconds_until_next_2am_est() -> int:
    now = datetime.now(ZoneInfo("America/New_York"))
    cutoff = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= cutoff:
        cutoff += timedelta(days=1)
    return int((cutoff - now).total_seconds())


def _fetch_from_nba_api(player_id: int, season: str, season_type: str) -> pd.DataFrame:
    """
    Call nba_api directly for one player-season, with retry/backoff on
    transient request failures (stats.nba.com is slow/flaky). Raises the
    last exception if every attempt fails.
    """
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id,
                season=season,
                season_type_all_star=season_type,
                proxy=NBA_API_PROXY,
            )
            return log.get_data_frames()[0]
        # requests.exceptions.RequestException covers network-level failures
        # (timeouts, connection errors). json.JSONDecodeError covers a
        # different failure mode: stats.nba.com (or the proxy in front of
        # it) returning a 200 OK with an empty/invalid body — nba_api's own
        # response.json() call raises this, and requests never sees it as
        # an error, so it needs to be retried here explicitly too.
        except (requests.exceptions.RequestException, json.JSONDecodeError):
            if attempt == MAX_RETRY_ATTEMPTS:
                raise
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))


def _fetch_game_log(
    redis_resources: RedisResources, player_id: int, season: str, season_type: str
) -> pd.DataFrame:
    """
    Fetch a player's stats for an entire season from cache, or fetch from
    nba_api and cache it if not yet in cache. If Redis itself is
    unreachable, fails open: fetch directly from nba_api instead of
    failing the whole request over what should be a pure optimization.
    """
    try:
        key = _cache_key(player_id, season, season_type)
        cached = redis_resources.client.get(key)
        if cached is not None:
            assert isinstance(cached, str)  # guaranteed by decode_responses=True
            return pd.read_json(io.StringIO(cached))

        lock_key = f"lock:{key}"
        token = str(uuid.uuid4())
        acquired = redis_resources.client.set(lock_key, token, nx=True, px=LOCK_TTL_MS)

        if acquired:
            try:
                df = _fetch_from_nba_api(player_id, season, season_type)
                redis_resources.client.set(key, df.to_json(), ex=_seconds_until_next_2am_est())
                return df
            finally:
                _release_lock(redis_resources, lock_key, token)
        else:
            deadline = time.monotonic() + MAX_WAIT_SECONDS
            while time.monotonic() < deadline:
                cached = redis_resources.client.get(key)
                if cached is not None:
                    assert isinstance(cached, str)  # guaranteed by decode_responses=True
                    return pd.read_json(io.StringIO(cached))
                if not redis_resources.client.exists(lock_key):
                    # The holder released the lock without ever populating
                    # the cache — it failed (e.g. nba_api errored out after
                    # its own retries), it didn't just finish. Waiting out
                    # the rest of the deadline for a cache entry that's
                    # never coming would be pointless, so fetch directly
                    # instead, same fallback as the RedisError case below.
                    return _fetch_from_nba_api(player_id, season, season_type)
                time.sleep(POLL_INTERVAL_SECONDS)
            raise ValueError("Player data is still loading, please try again later.")
    except redis.exceptions.RedisError:
        return _fetch_from_nba_api(player_id, season, season_type)


def _season_before(season: str) -> str:
    """ "1989-90" -> "1988-89"."""
    start_year = int(season[:4]) - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def resolve_player_id(player_name: str) -> int | None:
    """
    Return the nba_api player ID for a name lookup, or None if not found.

    Raises ValueError if `player_name` matches more than one player (e.g.
    "Jordan", "Williams") — rather than silently guessing, this surfaces
    the candidates in the exception message. agent.py's loop already
    turns any ValueError from a tool call into an is_error tool_result,
    so this reaches the model as "this call failed, here's why" — see
    agent.py's SYSTEM_PROMPT for the rule telling it to ask the user to
    clarify rather than guess or retry blindly.
    """
    matches = players.find_players_by_full_name(player_name)
    if not matches:
        return None
    if len(matches) > 1:
        candidates = ", ".join(
            f"{m['full_name']} ({'active' if m['is_active'] else 'retired'})" for m in matches
        )
        raise ValueError(
            f"'{player_name}' matches multiple players: {candidates}. "
            "Ask the user which one they meant, then call this tool again "
            "with that player's full name."
        )
    return matches[0]["id"]


def get_recent_stats(
    redis_resources: RedisResources,
    player_name: str,
    stats: list[str],
    n_games: int = 15,
    season: str | None = None,
) -> dict[str, list[int]]:
    """
    Return a player's values for each stat in `stats` over their most recent
    `n_games`, combining regular season and playoff games. `season` defaults
    to the current season (resolved per call — see current_season()).

    Raises ValueError if a stat is unsupported or the player isn't found.
    """
    if season is None:
        season = current_season()
    stats = [s.lower() for s in stats]
    unknown = [s for s in stats if s not in STAT_COLUMNS]
    if unknown:
        raise ValueError(f"Unsupported stat(s) {unknown}. Supported: {list(STAT_COLUMNS)}")
    if not 1 <= n_games <= MAX_N_GAMES:
        raise ValueError(f"n_games must be between 1 and {MAX_N_GAMES}, got {n_games}")

    player_id = resolve_player_id(player_name)
    if player_id is None:
        raise ValueError(f"Player '{player_name}' not found")

    # playergamelog always returns a whole season per call — there's no way to
    # ask it for fewer games. So this loop isn't about limiting fetch size; it
    # walks backward season by season because n_games can exceed what a single
    # season (even with playoffs) contains, e.g. a long-career player's request
    # for more games than they played that season.
    values: dict[str, list[int]] = {stat: [] for stat in stats}
    season_cursor = season
    while len(values[stats[0]]) < n_games:
        found_this_season = False
        season_types = (
            SeasonTypePlayoffs.playoffs,
            SeasonTypePlayoffs.playin,
            SeasonTypePlayoffs.regular,
        )
        for season_type in season_types:
            if len(values[stats[0]]) >= n_games:
                break  # playoffs alone covered it — skip the regular-season call
            df = _fetch_game_log(redis_resources, player_id, season_cursor, season_type)
            found_this_season = found_this_season or not df.empty
            remaining = n_games - len(values[stats[0]])
            sliced = df.head(remaining)
            for stat in stats:
                values[stat].extend(int(v) for v in sliced[STAT_COLUMNS[stat]])

        if not found_this_season:
            break  # no data this season or earlier — before the player's career
        season_cursor = _season_before(season_cursor)

    if not values[stats[0]]:
        raise ValueError(f"No games found for {player_name} in or before {season}")

    return values


def get_full_season_stats(
    redis_resources: RedisResources,
    player_name: str,
    stats: list[str],
    season: str | None = None,
) -> dict[str, list[int]]:
    """
    Returns a player's values for each stat in 'stats'
    for the current or last finished season. `season` defaults to the
    current season (resolved per call — see current_season()).

    Raises ValueError if a stat is unsupported or the player isn't found.
    """
    if season is None:
        season = current_season()
    stats = [s.lower() for s in stats]
    unknown = [s for s in stats if s not in STAT_COLUMNS]
    if unknown:
        raise ValueError(f"Unsupported stat(s) {unknown}. Supported: {list(STAT_COLUMNS)}")

    player_id = resolve_player_id(player_name)
    if player_id is None:
        raise ValueError(f"Player '{player_name}' not found")

    values: dict[str, list[int]] = {stat: [] for stat in stats}
    season_types = (
        SeasonTypePlayoffs.playoffs,
        SeasonTypePlayoffs.playin,
        SeasonTypePlayoffs.regular,
    )
    for season_type in season_types:
        df = _fetch_game_log(redis_resources, player_id, season, season_type)
        for stat in stats:
            values[stat].extend(int(v) for v in df[STAT_COLUMNS[stat]])

    if not values[stats[0]]:
        raise ValueError(f"No games found for {player_name} in or before {season}")

    return values


if __name__ == "__main__":
    _redis_url = getenv("REDIS_URL")
    if not _redis_url:
        raise RuntimeError("REDIS_URL must be set.")
    _redis_client = redis.Redis.from_url(_redis_url, decode_responses=True)
    _redis_resources = build_redis_resources(_redis_client)
    recent = get_recent_stats(
        _redis_resources, "Jokic", ["points", "rebounds"], n_games=15, season="2025-26"
    )
    print("Recent points:", recent)
    print("Games returned:", len(recent.get("points", [])))
