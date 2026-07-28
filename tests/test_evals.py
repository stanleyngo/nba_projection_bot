"""
test_evals.py — Stage 7: evaluating the AGENT's behavior, not the math.

test_simulation.py and test_backtest.py check simulation.py's deterministic
math — fixed inputs always produce the same, exactly-checkable output. This
file checks something different: whether the LLM agent, running through the
real tool-use loop, actually follows the rules baked into SYSTEM_PROMPT
(agent.py) — picks the right tool, never fabricates a number, attributes
opinions correctly, and so on. Since a model's output isn't deterministic
the way project_stat's math is, "correct" here means "satisfies a rubric,"
checked two different ways:
  - Deterministic checks: did the agent's TOOL CALLS match what's expected?
    (agent.run_agent's new `trace` parameter makes this visible — see
    agent.py.) This is cheap, fast, and exact.
  - LLM-as-judge checks: for things too fuzzy for exact string matching
    (e.g. "was every opinion properly attributed"), a SECOND, separate
    Claude call grades the agent's actual response text against a yes/no
    rubric. Slower and costs real tokens, so use sparingly — only for
    rules that genuinely can't be checked by trace/string matching alone.

These tests make real, billed Anthropic API calls (at least one call per
scenario, often several per tool-use round trip, plus a judge call for
rubric-based checks) and depend on live NBA data being available — they are
NOT run by default with a plain `pytest` invocation. They're marked with
the custom "eval" marker (registered in pyproject.toml/pytest.ini) and only
run when explicitly requested: `pytest -m eval`. Run them deliberately —
after a SYSTEM_PROMPT change, before a release — not on every save.
"""

# 1. Imports you'll need:
#    - pytest
#    - anthropic (for the judge call — a plain AsyncAnthropic client, same
#      as agent.py uses)
#    - nba_projection_bot.agent as agent

import anthropic
import pytest

from nba_projection_bot import agent as agent
from nba_projection_bot import db as db

# 2. SCENARIOS: a list of dicts, one per test case. Each needs:
#      "prompt": the user question to send through run_agent
#      "expected_tools": list of tool names that MUST appear somewhere in
#         the trace (empty list means "no tools should be called at all",
#         e.g. an off-topic question)
#      "forbidden_tools": list of tool names that must NOT appear (e.g. a
#         standalone injury question shouldn't trigger a projection call)
#      "judge_rubric": optional — a yes/no question for the LLM judge to
#         answer about the FINAL TEXT response, phrased so "YES" always
#         means "this passed" (keep the polarity consistent across all
#         rubrics, so a grading bug doesn't silently invert results)

scenarios = []
#
#    A starter set, each one testing a rule that's actually in
#    SYSTEM_PROMPT right now:
#
#    - Off-topic question ("What's the weather like today?")
#        expected_tools: [], forbidden_tools: ["project_stat_over_line", ...]
#        (the model should decline, not call anything)

scenarios.append({"prompt": "What's the weather like today?",
                   "expected_tools": [], "forbidden_tools": ["project_stat_over_line", "project_combo_over_line", "web_search"]})
#
#    - Standard single-stat projection with a line
#        ("What's Jokic projected for points against a 25.5 line?")
#        expected_tools: ["project_stat_over_line"]

scenarios.append({"prompt": "What's Jokic projected for points against a 25.5 line?",
                   "expected_tools": ["project_stat_over_line"], "forbidden_tools": ["project_combo_over_line"]})
#
#    - A combined prop ("What's Jokic's PRA prop against 45.5?")
#        expected_tools: ["project_combo_over_line"]
#        forbidden_tools: ["project_stat_over_line"]  — should NOT be three
#        separate single-stat calls instead

scenarios.append({"prompt": "What's Jokic's PRA prop against 45.5?",
                   "expected_tools": ["project_combo_over_line"], "forbidden_tools": ["project_stat_over_line"]})
#
#    - Standalone availability question ("Is Wembanyama playing tonight?")
#        expected_tools: ["web_search"]
#        forbidden_tools: ["project_stat_over_line", "project_combo_over_line"]
#
scenarios.append({"prompt": "Is Wembanyama playing tonight?",
                   "expected_tools": ["web_search"], "forbidden_tools": ["project_stat_over_line", "project_combo_over_line"]})
#    - Analysis attribution rubric — reuse the projection scenario's
#      prompt, or a news-heavy one, with:
#        judge_rubric: "Does every piece of analyst/sportswriter opinion in
#        this response get explicitly attributed to a source (e.g.
#        'according to X', 'one analyst believes'), rather than stated as
#        settled fact? If there is no opinion content in the response at
#        all, answer YES (nothing to violate). Answer only YES or NO."

judge_rubric = "Does every piece of analyst/sportswriter opinion in this response get explicitly attributed to a source (e.g. 'according to X', 'one analyst believes'), rather than stated as settled fact? If there is no opinion content in the response at all, answer YES (nothing to violate). Answer only YES or NO."
scenarios.append({"prompt": "What is the recent discussion around Jokic?",
                   "expected_tools": ["get_player_news_context"], "forbidden_tools": ["project_stat_over_line", "project_combo_over_line", "web_search"], "judge_rubric": judge_rubric})
# 3. async def judge(response_text: str, rubric: str) -> bool
#    Send `rubric` + `response_text` to Claude in a SEPARATE, minimal call
#    (small max_tokens, no tools) asking it to answer strictly "YES" or
#    "NO" — then return True/False based on that answer. Keep the judge
#    prompt narrow and literal ("Answer with exactly one word: YES or NO")
#    so parsing its answer is simple and reliable, not another judgment
#    call on your end.

async def judge(response_text: str, rubric: str) -> bool:
    """
    Ask Claude to grade `response_text` against a yes/no `rubric`.

    Returns True if Claude answered "YES", False if "NO". Raises ValueError
    if the answer is anything else (shouldn't happen if the prompt is
    written carefully).
    """
    client = anthropic.AsyncAnthropic()
    prompt = """
You are a helpful assistant that grades text against a rubric. Answer with exactly one word: YES or NO."""
    message = f"{prompt}\n\nRubric: {rubric}\n\nResponse: {response_text}\n\nAnswer:"
    response = await client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": message}],
        temperature=0.0,
    )
    answer = response.content[0].text.strip().upper()
    if answer == "YES":
        return True
    elif answer == "NO":
        return False
    else:
        raise ValueError(f"Unexpected judge answer: {answer}")
# 4. The test function — parametrize over SCENARIOS so each one shows up
#    as its own named test in pytest's output rather than one big loop
#    that stops at the first failure:
#
#      @pytest.mark.eval
#      @pytest.mark.asyncio
#      @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["prompt"])
#      async def test_scenario(scenario):
#          trace = []
#          answer, _ = await agent.run_agent(scenario["prompt"], trace=trace)
#          called = {t["tool"] for t in trace}
#
#          for tool in scenario.get("expected_tools", []):
#              assert tool in called, f"expected {tool} to be called, trace={trace}"
#          for tool in scenario.get("forbidden_tools", []):
#              assert tool not in called, f"{tool} should NOT have been called, trace={trace}"
#          if "judge_rubric" in scenario:
#              assert await judge(answer, scenario["judge_rubric"]), answer
#
#    (You'll need pytest-asyncio installed and configured for async test
#    functions to work — check if it's already in requirements.txt; if
#    not, `pip install pytest-asyncio` and add `asyncio_mode = auto` to
#    pyproject.toml/pytest.ini alongside the marker registration below.)

@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", scenarios, ids=lambda s: s["prompt"])
async def test_scenario(scenario):
    # A stable throwaway user for the eval suite — not real auth, just
    # something to satisfy Conversation's user_id foreign key.
    # get_or_create_user is idempotent, so this is cheap on every call.
    user_id = await db.get_or_create_user("eval-suite", "eval-suite@local")
    trace = []
    answer, _, _, _ = await agent.run_agent(scenario["prompt"], user_id, trace=trace)
    called = {t["tool"] for t in trace}

    for tool in scenario.get("expected_tools", []):
        assert tool in called, f"expected {tool} to be called, trace={trace}"
    for tool in scenario.get("forbidden_tools", []):
        assert tool not in called, f"{tool} should NOT have been called, trace={trace}"
    if "judge_rubric" in scenario:
        assert await judge(answer, scenario["judge_rubric"]), answer
