"""Tests for the projection-collection helper in agent.py.

The agent loop itself hits the real (billed) Anthropic API and the DB, so it
isn't unit-tested here. The card-surfacing logic is factored into a pure helper
(`projection_record`) that we CAN test in isolation — given a projection tool's
name, its input, and its result dict, it builds the record the API hands to the
frontend.
"""

import asyncio

import nba_projection_bot.agent as agent
import nba_projection_bot.tools as tools


def test_projection_record_single_stat():
    record = agent.projection_record(
        "project_stat_over_line",
        {"player_name": "Nikola Jokic", "stat": "points", "line": 25.5},
        {"mean": 28.3, "median": 28.0, "model": "negative_binomial",
         "prob_over": 0.64, "prob_under": 0.36, "prob_push": 0.0},
    )
    assert record["player_name"] == "Nikola Jokic"
    assert record["stat"] == "points"
    assert record["line"] == 25.5
    assert record["result"]["prob_over"] == 0.64


def test_projection_record_combo_joins_stats():
    record = agent.projection_record(
        "project_combo_over_line",
        {"player_name": "LeBron James",
         "stats": ["points", "rebounds", "assists"], "line": 45.5},
        {"mean": 46.1, "median": 46.0, "model": "poisson",
         "prob_over": 0.55, "prob_under": 0.45, "prob_push": 0.0},
    )
    assert record["stat"] == "points+rebounds+assists"
    assert record["line"] == 45.5


def test_projection_record_omitted_line_is_none():
    record = agent.projection_record(
        "project_stat_over_line",
        {"player_name": "Luka Doncic", "stat": "assists"},
        {"mean": 8.9, "median": 9.0, "model": "poisson"},
    )
    assert record["line"] is None


def test_is_projection_tool():
    assert agent.is_projection_tool("project_stat_over_line")
    assert agent.is_projection_tool("project_combo_over_line")
    assert not agent.is_projection_tool("get_player_news_context")
    assert not agent.is_projection_tool("web_search")


def test_is_news_tool():
    assert agent.is_news_tool("get_player_news_context")
    assert not agent.is_news_tool("project_stat_over_line")
    assert not agent.is_news_tool("web_search")


def test_news_record_passes_through_lists():
    record = agent.news_record(
        {"player_name": "LeBron James"},
        {"news": [{"text": "listed questionable", "url": "https://x.com/a", "title": "Injury"}],
         "analysis": [{"text": "expects a bounce-back", "url": "https://x.com/b", "title": "Outlook"}]},
    )
    assert record["player_name"] == "LeBron James"
    assert record["news"][0]["url"] == "https://x.com/a"
    assert record["analysis"][0]["title"] == "Outlook"


def test_news_record_handles_empty_result():
    record = agent.news_record({"player_name": "Nobody"}, {})
    assert record["player_name"] == "Nobody"
    assert record["news"] == []
    assert record["analysis"] == []


# ---------------------------------------------------------------------------
# ensure_player_news — deterministic news backfill so a news card shows for
# every projected player regardless of whether the model fetched it.
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_ensure_player_news_backfills_uncovered_player(monkeypatch):
    async def fake_news(player_name):
        return {"news": [{"text": "note", "url": "https://x/1", "title": "T"}],
                "analysis": []}
    monkeypatch.setattr(tools, "get_player_news_context", fake_news)

    projections = [{"player_name": "Nikola Jokic", "stat": "points",
                    "line": 25.5, "result": {"mean": 28.3}}]
    news: list[dict] = []
    _run(agent.ensure_player_news(projections, news))

    assert len(news) == 1
    assert news[0]["player_name"] == "Nikola Jokic"
    assert news[0]["news"][0]["url"] == "https://x/1"


def test_ensure_player_news_skips_already_covered_player(monkeypatch):
    calls = []

    async def fake_news(player_name):
        calls.append(player_name)
        return {"news": [], "analysis": []}
    monkeypatch.setattr(tools, "get_player_news_context", fake_news)

    projections = [{"player_name": "LeBron James", "stat": "points",
                    "line": 27.5, "result": {"mean": 26.0}}]
    news = [{"player_name": "LeBron James", "news": [], "analysis": []}]
    _run(agent.ensure_player_news(projections, news))

    assert calls == []       # already covered — no fetch
    assert len(news) == 1     # no duplicate appended


def test_ensure_player_news_swallows_fetch_errors(monkeypatch):
    async def boom(player_name):
        raise RuntimeError("tavily down")
    monkeypatch.setattr(tools, "get_player_news_context", boom)

    projections = [{"player_name": "Luka Doncic", "stat": "assists",
                    "line": 8.5, "result": {"mean": 9.0}}]
    news: list[dict] = []
    _run(agent.ensure_player_news(projections, news))  # must not raise

    assert news == []
