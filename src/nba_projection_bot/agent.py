"""
agent.py

This module's job: own the back-and-forth with the Anthropic API — send the
conversation history plus tools.get_tool_schemas() to the model, and when
the model responds with a tool_use block, call tools.call_tool(...) and
feed the result back, repeating until the model returns a final text answer
instead of another tool call.

"""

import asyncio
import json
import logging

import anthropic
from dotenv import load_dotenv

import nba_projection_bot.db as db
import nba_projection_bot.tools as tools
from nba_projection_bot.prompts import SYSTEM_PROMPT

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2048
MAX_TOOL_ITERATIONS = 7

# The projection-family tools whose results the UI renders as visual cards.
# web_search and recent_stats produce prose/raw context, not a card, so they're
# excluded here; the news tool gets its own card (see NEWS_TOOL below).
PROJECTION_TOOLS = frozenset({"project_stat_over_line", "project_combo_over_line"})

# The RAG tool whose news/analysis result the UI renders as a News & Analysis card.
NEWS_TOOL = "get_player_news_context"


def is_projection_tool(name: str) -> bool:
    """True if `name` is a projection tool whose result the UI cards render."""
    return name in PROJECTION_TOOLS


def is_news_tool(name: str) -> bool:
    """True if `name` is the news tool whose result the UI news card renders."""
    return name == NEWS_TOOL


def news_record(tool_input: dict, result: dict) -> dict:
    """
    Build the news-card record the API hands to the frontend for one
    get_player_news_context call: the player asked about, paired with the two
    retrieved lists. `news` items are reported facts and `analysis` items are
    opinion — the UI must keep them visually separate and label analysis as
    opinion (mirroring the framing rules in SYSTEM_PROMPT). Either list may be
    empty, which is expected (nothing relevant found), not an error.
    """
    return {
        "player_name": tool_input.get("player_name"),
        "news": result.get("news", []),
        "analysis": result.get("analysis", []),
    }


TITLE_MODEL = "claude-haiku-4-5"


async def generate_title(client: anthropic.AsyncAnthropic, user_message: str, answer: str) -> str:
    """
    Generate a short (3-6 word) title for a new conversation, for the
    sidebar. Uses a cheap/fast model — this is a small auxiliary task, not
    something that needs the main model's full capability.
    """
    response = await client.messages.create(
        model=TITLE_MODEL,
        max_tokens=20,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize this exchange as a short 3-6 word title for a "
                    "conversation list, like a chat app would show. Respond with "
                    "ONLY the title itself — no quotes, no trailing punctuation, "
                    "nothing else.\n\n"
                    f"Question: {user_message}\n\nAnswer: {answer}"
                ),
            }
        ],
    )
    text_blocks = [block for block in response.content if block.type == "text"]
    if not text_blocks:
        return user_message[:50]  # fallback: just truncate the question itself
    return text_blocks[0].text.strip()


async def ensure_player_news(projections: list[dict], news: list[dict]) -> None:
    """
    Guarantee a news card for every projected player, in place.

    News (and any injury info it carries) must show up regardless of whether the
    model chose to fetch it — so for each unique player that has a projection but
    no news record yet, fetch it here and append. A fetch failure is swallowed:
    news is supplementary context and must never break the projection answer.
    """
    covered = {record.get("player_name") for record in news}
    for projection in projections:
        name = projection.get("player_name")
        if not name or name in covered:
            continue
        covered.add(name)
        try:
            context = await tools.get_player_news_context(name)
        except Exception:  # network/keys/etc. — supplementary, don't fail the turn
            logging.exception(f"News backfill fetch failed for {name!r}")
            continue
        news.append(news_record({"player_name": name}, context))


def projection_record(name: str, tool_input: dict, result: dict) -> dict:
    """
    Build the card record the API hands to the frontend for one projection
    tool call: the player/stat/line the model asked about, paired with the
    engine's result dict (mean, median, model, prob_over/under/push, injury
    fields). For combo props the component stats are joined (e.g.
    "points+rebounds+assists") into a single `stat` label; `line` is None when
    the model asked for a general projection with no over/under line.
    """
    if name == "project_combo_over_line":
        stat = "+".join(tool_input.get("stats", []))
    else:
        stat = tool_input["stat"]
    return {
        "player_name": tool_input.get("player_name"),
        "stat": stat,
        "line": tool_input.get("line"),
        "result": result,
    }


async def run_agent(
    user_message: str,
    user_id: int,
    conversation_id: int | None = None,
    trace: list[dict] | None = None,
) -> tuple[str, int, list[dict], list[dict]]:
    """
    Run one user turn through the agent loop: send `user_message` to the
    model (with prior turns from `conversation_id` loaded as context, if
    given), resolve any tool calls it makes, and return its final text
    response once it's done calling tools.

    `user_id` must be the CALLER-VERIFIED internal user id (from
    db.get_or_create_user, after verifying a Google ID token — see
    api.py) — never a raw, client-supplied value. Used both to record
    ownership when starting a brand-new conversation, and to verify
    ownership of an EXISTING conversation_id on every load/append —
    db.load_history and db.append_message both raise PermissionError if
    conversation_id doesn't actually belong to user_id, so a caller can't
    read or write into someone else's conversation just by knowing or
    guessing its id.

    Returns (answer, conversation_id, projections, news):
    - conversation_id is echoed back (or newly created, if it was None) so
      the caller can pass it on the NEXT call to keep the same conversation
      going. An HTTP request has no memory of its own; this id is the only
      thing that ties separate requests back into one conversation.
    """

    load_dotenv()
    anthropic_client = anthropic.AsyncAnthropic()

    is_new_conversation = conversation_id is None
    if conversation_id is None:
        conversation_id = await db.create_conversation(user_id)
    history = await db.load_history(conversation_id, user_id)
    messages = history + [{"role": "user", "content": user_message}]
    projections: list[dict] = []
    news: list[dict] = []
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await anthropic_client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            # Plain dicts are correct here at runtime — the SDK validates/
            # converts them internally
            tools=tools.get_tool_schemas(),  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            text_blocks = [block for block in response.content if block.type == "text"]
            if not text_blocks:
                raise ValueError("Expected at least one text block in the final response.")
            # web_search citations split the answer into multiple text blocks,
            # one per cited segment — a block boundary marks a citation
            # attachment point, not an intentional paragraph break. The model
            # puts any real paragraph break inside a block's own text, so
            # blocks must be concatenated directly, not joined with "\n\n".
            answer = "".join(block.text for block in text_blocks)
            # Always surface news for any projected player, even if the model
            # didn't pull it during the loop.
            await ensure_player_news(projections, news)
            await db.append_message(conversation_id, user_id, "user", user_message)
            await db.append_message(
                conversation_id,
                user_id,
                "assistant",
                answer,
                projections=projections,
                news=news,
            )
            if is_new_conversation:
                try:
                    title = await generate_title(anthropic_client, user_message, answer)
                    await db.set_conversation_title(conversation_id, title)
                except Exception:
                    logging.exception(f"Title generation failed for conversation {conversation_id}")
            return answer, conversation_id, projections, news
        tool_results: list[dict[str, str | bool]] = []
        for block in response.content:
            if block.type == "tool_use":
                if trace is not None:
                    trace.append({"tool": block.name, "input": block.input})
                try:
                    result = await tools.call_tool(block.name, block.input)
                    if is_projection_tool(block.name):
                        projections.append(projection_record(block.name, block.input, result))
                    elif is_news_tool(block.name):
                        news.append(news_record(block.input, result))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
                except (ValueError, TypeError) as e:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "is_error": True,
                            "content": str(e),
                        }
                    )
        messages.append({"role": "user", "content": tool_results})
    raise RuntimeError(
        f"Unable to generate a response after {MAX_TOOL_ITERATIONS} tool-use "
        "iterations. Please try again later."
    )


if __name__ == "__main__":
    answer, conversation_id, projections, news = asyncio.run(
        run_agent("What's Nikola Jokic projected for against a 25.5 point line?", user_id=1)
    )
    print(answer, conversation_id)
    print("projections:", projections)
    print("news:", news)
