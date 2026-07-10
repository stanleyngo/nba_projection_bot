"""
data.py — Stage 1: fetch NBA game-log data from nba_api.

This module's job: given a player name and a stat, return that player's values
over their most recent N games. That list is the raw material the Monte Carlo
engine (simulation.py) samples from.

NOTE: nba_api calls stats.nba.com, which can rate-limit, time out, or change
its response shape. If something breaks, verify function names against the
current nba_api docs. Later (Stage 5) you'll add retries / error handling and
possibly a fallback data source.
"""

from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.library.parameters import SeasonTypePlayoffs

STAT_COLUMNS = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "steals": "STL",
    "blocks": "BLK",
    "threes": "FG3M",
}


def _season_before(season: str) -> str:
    """"1989-90" -> "1988-89"."""
    start_year = int(season[:4]) - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def resolve_player_id(player_name: str) -> int | None:
    """Return the nba_api player ID for a name lookup, or None if not found."""
    matches = players.find_players_by_full_name(player_name)
    if not matches:
        return None
    # NOTE: this lookup can return multiple players for ambiguous or partial
    # input (for example, "Jordan" or "Smith"). For now we take the first
    # match. TODO (Stage 5): handle ambiguity by asking the user which one.
    return matches[0]["id"]


def get_recent_stats(
    player_name: str,
    stats: list[str],
    n_games: int = 15,
    season: str = "2025-26",
) -> dict[str, list[int]]:
    """
    Return a player's values for each stat in `stats` over their most recent
    `n_games`, combining regular season and playoff games.

    Raises ValueError if a stat is unsupported or the player isn't found.
    """
    stats = [s.lower() for s in stats]
    unknown = [s for s in stats if s not in STAT_COLUMNS]
    if unknown:
        raise ValueError(
            f"Unsupported stat(s) {unknown}. Supported: {list(STAT_COLUMNS)}"
        )

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
            log = playergamelog.PlayerGameLog(
                player_id=player_id, season=current_season, season_type_all_star=season_type
            )
            df = log.get_data_frames()[0]
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
