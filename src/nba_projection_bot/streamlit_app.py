"""
streamlit_app.py — Stage 5: a simple web UI for the bot, calling api.py.

This module's job: give you something to actually click around in instead
of curl or /docs. It calls your FastAPI backend's /ask endpoint over HTTP —
it does NOT import agent.py directly. That's deliberate: this UI talks to
the API exactly the way any other real client would, which also means
api.py needs to be running separately (uvicorn) before this UI will work.

NOTE: like api.py itself, every question you submit here triggers a real,
billed Anthropic API call on the backend — this isn't a free UI, and it can
take several seconds (possibly multiple tool round-trips).
"""

import requests
import streamlit as st

API_URL = "http://127.0.0.1:8000/ask"


def ask_backend(question: str) -> str:
    """
    POST `question` to the FastAPI backend's /ask endpoint and return the
    answer string.

    Raises RuntimeError with a clean message on any failure — connection
    issues, a non-2xx response, or an unexpected response shape — so the
    caller (the UI code below) has exactly one exception type to handle,
    regardless of what actually went wrong underneath.
    """
    # 1. requests.post(API_URL, json={"question": question}, timeout=...).
    #    Remember api.py's own docstring: this can take several seconds
    #    with multiple tool round-trips — web_search specifically adds real
    #    latency (server-side searches, possible pause_turn retries across
    #    several loop iterations), so don't set the timeout too short
    #    (120 seconds is a reasonable floor now that web search is in play).
    #
    # 2. Wrap the request in try/except requests.RequestException — this
    #    covers connection errors, timeouts, DNS failures, etc. (e.g. the
    #    API not running at all). On failure, raise RuntimeError with a
    #    clear message, e.g. f"Could not reach the API: {e}".
    #
    # 3. Check whether the response was actually successful. If not:
    #      - Try to pull the "detail" field out of the JSON body — that's
    #        the clean error message every HTTPException in api.py's ask()
    #        endpoint includes (400/500/502 all use this same shape).
    #      - Raise RuntimeError with that detail message. Fall back to a
    #        generic message if the body isn't parseable JSON for some
    #        reason (a truly unexpected failure shouldn't itself crash
    #        this function).
    #    (response.raise_for_status() raises requests.HTTPError on a bad
    #    status code — you can catch that alongside RequestException, or
    #    check response.ok / response.status_code manually before calling
    #    .json(). Either approach works; pick one.)
    #
    # 4. On success, parse the JSON body and return its "answer" field
    #    (matches api.py's AskResponse model exactly).
    try:
        response = requests.post(API_URL, json={"question": question}, timeout=120)
        response.raise_for_status()
        data = response.json()
        return data["answer"]
    except requests.HTTPError as e:
        try:
            error_detail = response.json().get("detail", "Unknown error")
        except (ValueError, KeyError):
            error_detail = "Unknown error"
        raise RuntimeError(f"API returned an error: {error_detail}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Request failed: {e}")
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"Unexpected response format: {e}")



st.title("NBA Prop Projection Bot")

# 1. Initialize st.session_state.history to an empty list if it's not
#    already there — same pattern as the reference file. This is what
#    lets past Q&A pairs stick around across Streamlit's re-runs.
#
# 2. A text input for the user's question (st.text_input(...)).
#
# 3. A button (st.button(...)). Inside its `if`:
#      a. Guard against an empty question — st.warning(...) and skip if
#         so, same as the reference file.
#      b. Wrap the actual call in st.spinner(...) — this can genuinely
#         take a while (a real LLM doing multiple tool round-trips), so a
#         loading indicator matters more here than in the toy reference.
#      c. Call ask_backend(question) inside try/except RuntimeError:
#           - On failure: st.error(str(e)).
#           - On success: append (question, answer) to
#             st.session_state.history.
#
# 4. Below that, loop over st.session_state.history (most recent first is
#    a reasonable choice, but up to you) and display each question/answer
#    pair with st.write(...) or similar.
if "history" not in st.session_state:
    st.session_state.history = []
st.session_state.question = st.text_input("Ask a question about NBA player projections:")
if st.button("Submit"):
    if not st.session_state.question:
        st.warning("Please enter a question.")
    else:
        with st.spinner("Getting answer..."):
            try:
                answer = ask_backend(st.session_state.question)
                st.session_state.history.append((st.session_state.question, answer))
            except RuntimeError as e:
                st.error(str(e))
for question, answer in reversed(st.session_state.history):
    st.write(f"**Q:** {question}")
    st.write(f"**A:** {answer}")
