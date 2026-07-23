"""
tools.py — Stage 3: wrap data.py / simulation.py as Anthropic tool-use tools.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import nba_projection_bot.data as data
import nba_projection_bot.rag as rag
import nba_projection_bot.simulation as simulation

@dataclass
class ToolEntry:
    description: str
    input_schema: dict
    function: Callable

TOOL_REGISTRY: dict[str, ToolEntry] = {}

# WEB_SEARCH_TOOL is a SERVER-SIDE tool — Anthropic runs the actual search
# on their own infrastructure, so unlike everything in TOOL_REGISTRY there's
# no Python function behind it and it's never dispatched through call_tool.
# It deliberately does NOT go through @register_tool/TOOL_REGISTRY (that
# registry's whole shape assumes a backing function to call) — it's just
# included directly in get_tool_schemas()'s returned list, so agent.py can
# treat "every tool the model should see" as one call to that function
# without needing to know this one exists as a special case.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


def register_tool(name: str, description: str, input_schema: dict):
    """
    Decorator factory — usage:

        @register_tool(
            "tool_name",
            "what it does, for the model to read",
            {"type": "object", "properties": {...}, "required": [...]},
        )
        def tool_name(...):
            ...

    Registers the decorated function into TOOL_REGISTRY under `name`,
    alongside its description/input_schema, so both the Anthropic-facing
    schema list (see get_tool_schemas) and the dispatcher (see call_tool)
    can be derived from this one registry.
    """

    def decorator(func):
        TOOL_REGISTRY[name] = ToolEntry(
            description=description,
            input_schema=input_schema,
            function=func,
        )
        return func
    return decorator


def get_tool_schemas() -> list[dict]:

    custom_schemas = [
        {
            "name": name,
            "description": entry.description,
            "input_schema": entry.input_schema,
        }
        for name, entry in TOOL_REGISTRY.items()
    ]
    return custom_schemas + [WEB_SEARCH_TOOL]


@register_tool(
    "get_player_recent_stats",
    "Fetch a player's recent per-game stat values for the last n_games.",
    {
        "type": "object",
        "properties": {
            "player_name": {"type": "string", "description": "The player's name (first, last, or full)."},
            "stats": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(data.STAT_COLUMNS),
                },
                "description": "A list of stat names to fetch (e.g., ['points', 'assists']).",
            },
            "n_games": {
                "type": "integer",
                "minimum": 1,
                "maximum": data.MAX_N_GAMES,
                "description": "The number of recent games to consider (default: 15).",
            },
        },
        "required": ["player_name", "stats"],
    },
)

async def get_player_recent_stats(player_name: str, stats: list[str], n_games: int = 15) -> dict:
    return await asyncio.to_thread(data.get_recent_stats, player_name, stats, n_games=n_games)


@register_tool(
    "project_stat_over_line",
    "Project a player's next-game performance for one stat. If a line is "
    "given, returns the probability of exceeding/falling under it (plus "
    "push probability); if no line is given, returns just the general "
    "projected mean/median with no line comparison.",
    {
        "type": "object",
        "properties": {
            "player_name": {"type": "string", "description": "The player's name (first, last, or full)."},
            "stat": {
                "type": "string",
                "enum": list(data.STAT_COLUMNS),
                "description": "The stat name to project (e.g., 'points').",
            },
            "line": {
                "type": "number",
                "description": (
                    "The stat line to compare against (e.g., 22.5). Optional — "
                    "omit this for a general projection with no over/under "
                    "comparison (e.g. 'what will he score' rather than 'will "
                    "he go over 25.5')."
                ),
            },
            "n_games": {
                "type": "integer",
                "minimum": 1,
                "maximum": data.MAX_N_GAMES,
                "description": "The number of recent games to consider for the projection (default: 15).",
            },
        },
        "required": ["player_name", "stat"],
    },
)

async def project_stat_over_line(player_name: str, stat: str, line: float | None = None, n_games: int = 15) -> dict:
    values_dict = await asyncio.to_thread(data.get_recent_stats, player_name, [stat], n_games=n_games)
    values = values_dict[stat.lower()]
    return simulation.project_stat(values, line)


@register_tool(
    "get_player_news_context",
    "Retrieve the most relevant recent news snippets AND analyst/sportswriter "
    "commentary about a player, for background/narrative color only. Returns "
    "two separate lists — 'news' (reported facts) and 'analysis' (opinion/ "
    "commentary) — which must be presented and framed differently, not "
    "treated as equally factual. Each item is an object with 'text', 'url', "
    "and 'title' — always cite the 'url' as a clickable link when presenting "
    "that item, so the user can read the original source. This is NOT a "
    "source of statistical data or projections — never use it in place of "
    "project_stat_over_line, only alongside it.",
    {
        "type": "object",
        "properties": {
            "player_name": {"type": "string", "description": "The player's name (first, last, or full)."},
        },
        "required": ["player_name"],
    },
)
async def get_player_news_context(player_name: str) -> dict:
    return await rag.get_relevant_context(player_name)


async def call_tool(name: str, tool_input: dict) -> dict:
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Unrecognized tool name: {name}")
    return await TOOL_REGISTRY[name].function(**tool_input)


if __name__ == "__main__": 
    import json

    schemas = get_tool_schemas()
    print("registered tool schemas:", json.dumps(schemas, indent=2))

    result = asyncio.run(call_tool(
        "project_stat_over_line",
        {"player_name": "Nikola Jokic", "stat": "points", "line": 25.5, "n_games": 15},
    ))
    print("call_tool result:", json.dumps(result, indent=2))

    print("rag results:", asyncio.run(get_player_news_context("Nikola Jokic")))
