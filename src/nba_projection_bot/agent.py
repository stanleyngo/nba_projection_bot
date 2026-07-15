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

import json

import anthropic
from dotenv import load_dotenv

import nba_projection_bot.tools as tools

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 1024
MAX_TOOL_ITERATIONS = 5


def run_agent(user_message: str) -> str:
    """
    Run one user turn through the agent loop: send `user_message` to the
    model, resolve any tool calls it makes, and return its final text
    response once it's done calling tools.
    """
   
    load_dotenv()
    anthropic_client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]
    for _ in range(MAX_TOOL_ITERATIONS):
        response = anthropic_client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=tools.get_tool_schemas(),
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            text_blocks = [block for block in response.content if block.type == "text"]
            if len(text_blocks) != 1:
                raise ValueError("Expected exactly one text block in the final response.")
            return text_blocks[0].text
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    result = tools.call_tool(block.name, block.input)
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
    # Stage 4 checkpoint — run `python -m nba_projection_bot.agent` (from
    # src/) once you've filled in run_agent. Unlike tools.py's checkpoint,
    # this DOES make real API calls, so make sure ANTHROPIC_API_KEY is set
    # in a .env file first. Try a prompt that should trigger a tool call,
    # and confirm the tool actually gets invoked (not just answered from
    # the model's general knowledge) — e.g. by temporarily adding a print
    # inside tools.call_tool to confirm it fires.
    reply = run_agent("What's Nikola Jokic projected for against a 25.5 point line?")
    print(reply)
