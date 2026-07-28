"""Tests for api.py — the page route and the response contract.

These avoid the app lifespan (which needs Postgres) by not entering the
TestClient as a context manager, and they never hit /ask (which would make a
real, billed agent call). They cover the parts that are pure HTTP wiring: the
chat page is served at /, and AskResponse carries the new projections field
with a backward-compatible default.
"""

from fastapi.testclient import TestClient

import nba_projection_bot.api as api

client = TestClient(api.app)


def test_index_serves_chat_page():
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "nba_projection" in res.text


def test_health_still_ok():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ask_response_projections_default_empty():
    resp = api.AskResponse(answer="hi", conversation_id=1)
    assert resp.projections == []


def test_ask_response_carries_projections():
    proj = [{"player_name": "Nikola Jokic", "stat": "points", "line": 25.5,
             "result": {"mean": 28.3, "prob_over": 0.64}}]
    resp = api.AskResponse(answer="hi", conversation_id=1, projections=proj)
    assert resp.projections[0]["result"]["prob_over"] == 0.64


def test_ask_response_news_default_empty():
    resp = api.AskResponse(answer="hi", conversation_id=1)
    assert resp.news == []


def test_ask_response_carries_news():
    news = [{"player_name": "LeBron James",
             "news": [{"text": "questionable", "url": "https://x.com/a", "title": "Injury"}],
             "analysis": [{"text": "bounce-back", "url": "https://x.com/b", "title": "Outlook"}]}]
    resp = api.AskResponse(answer="hi", conversation_id=1, news=news)
    assert resp.news[0]["news"][0]["url"] == "https://x.com/a"
