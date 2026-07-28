"""
data.py

This module's job: given a player name and a stat, return that player's values
over their most recent N games.

NOTE: nba_api calls stats.nba.com, which can rate-limit, time out, or change
its response shape. If something breaks, verify function names against the
current nba_api docs.
"""

import time
from datetime import date

import pandas as pd
import requests
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.library.parameters import SeasonTypePlayoffs
from nba_api.stats.static import players

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

_today = date.today()
_season_start_year = _today.year if _today.month >= 11 else _today.year - 1
CURRENT_SEASON = f"{_season_start_year}-{str(_season_start_year + 1)[-2:]}"


def _fetch_game_log(player_id: int, season: str, season_type) -> pd.DataFrame:
    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        try:
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=season, season_type_all_star=season_type
            )
            return log.get_data_frames()[0]
        except requests.exceptions.RequestException:
            if attempt == MAX_RETRY_ATTEMPTS:
                raise
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))


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
    player_name: str,
    stats: list[str],
    n_games: int = 15,
    season: str = CURRENT_SEASON,
) -> dict[str, list[int]]:
    """
    Return a player's values for each stat in `stats` over their most recent
    `n_games`, combining regular season and playoff games.

    Raises ValueError if a stat is unsupported or the player isn't found.
    """
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
    current_season = season
    while len(values[stats[0]]) < n_games:
        found_this_season = False
        for season_type in (SeasonTypePlayoffs.playoffs, SeasonTypePlayoffs.regular):
            if len(values[stats[0]]) >= n_games:
                break  # playoffs alone covered it — skip the regular-season call
            df = _fetch_game_log(player_id, current_season, season_type)
            found_this_season = found_this_season or not df.empty
            remaining = n_games - len(values[stats[0]])
            sliced = df.head(remaining)
            for stat in stats:
                values[stat].extend(int(v) for v in sliced[STAT_COLUMNS[stat]])

        if not found_this_season:
            break  # no data this season or earlier — before the player's career
        current_season = _season_before(current_season)

    if not values[stats[0]]:
        raise ValueError(f"No games found for {player_name} in or before {season}")

    return values


if __name__ == "__main__":
    recent = get_recent_stats("Jokic", ["points", "rebounds"], n_games=15, season="2025-26")
    print("Recent points:", recent)
    print("Games returned:", len(recent.get("points", [])))
