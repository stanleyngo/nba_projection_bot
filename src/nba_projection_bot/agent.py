"""
agent.py — Stage 4: the conversation loop that ties the LLM to the tools.

This module's job: own the back-and-forth with the Anthropic API — send the
conversation history plus tools.get_tool_schemas() to the model, and when
the model responds with a tool_use block, call tools.call_tool(...) and
feed the result back, repeating until the model returns a final text answer
instead of another tool call.

NOTE: this uses the raw anthropic SDK (client.messages.create()), not a
framework — every step of the loop is explicit here rather than hidden
behind an abstraction. That's the point for a first LLM project: you should
be able to see exactly what gets sent and received at each turn, rather
than trusting a framework to do it for you.
"""

import asyncio
import json

import anthropic
from dotenv import load_dotenv

import nba_projection_bot.tools as tools

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1024
MAX_TOOL_ITERATIONS = 5

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}

SYSTEM_PROMPT = """You are an NBA player stat projection assistant. You help \
users understand the likelihood of a player exceeding a given stat line \
(points, rebounds, assists, steals, blocks, or threes) based on their \
recent game performance.

Rules:
- Only answer questions about NBA player stat projections. If asked \
anything unrelated (general chat, other sports, coding help, etc.), \
politely decline and explain what you can help with instead.
- Never estimate, guess, or calculate a projection or probability \
yourself. Always use the provided tools to get real data and run the \
simulation — you are a router and explainer, not a calculator.
- When you give a projection, mention that it's based on a simple \
statistical model (resampling from recent games), not a sophisticated \
predictive model — be upfront about that limitation.
- Present projections as statistical information, not betting advice. \
Never tell the user whether they should bet on something.
- The projection tool returns results from multiple simulation methods. \
Summarize the consensus across methods in plain language, and call out \
any meaningful disagreement between them — don't just report one method's \
numbers and ignore the rest.
- You have a web_search tool. Use it sparingly, only when it would \
materially affect the answer — e.g. checking whether a player is \
questionable/out with an injury, or on a back-to-back — not for general \
background. When you do use it, weave what you find into your \
explanation alongside the statistical projection (e.g. "he's projected \
at 25.7 points, though he's currently listed questionable with an ankle \
issue"). The statistical projection itself must still come only from the \
projection tool, never from search results or your own estimate.
- Standalone questions about a player's availability or injury status \
(without a specific stat line) are also in scope — answer these using \
web_search alone, without necessarily calling the projection tool.
"""

async def run_agent(user_message: str) -> str:
    """
    Run one user turn through the agent loop: send `user_message` to the
    model, resolve any tool calls it makes, and return its final text
    response once it's done calling tools.
    """

    load_dotenv()
    anthropic_client = anthropic.AsyncAnthropic()
    messages = [{"role": "user", "content": user_message}]
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await anthropic_client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
        
            tools=tools.get_tool_schemas() + [WEB_SEARCH_TOOL],
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            text_blocks = [block for block in response.content if block.type == "text"]
            if not text_blocks:
                raise ValueError("Expected at least one text block in the final response.")
            return "\n\n".join(block.text for block in text_blocks)
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = await tools.call_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })
                except (ValueError, TypeError) as e:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": True,
                        "content": str(e),
                    })
        messages.append({"role": "user", "content": tool_results})
    raise RuntimeError(
        f"Unable to generate a response after {MAX_TOOL_ITERATIONS} tool-use "
        "iterations. Please try again later."
    )


if __name__ == "__main__":
    reply = asyncio.run(run_agent("What's Nikola Jokic projected for against a 25.5 point line?"))
    print(reply)
