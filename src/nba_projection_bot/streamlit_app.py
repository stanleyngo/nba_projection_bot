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


def ask_backend(question: str, conversation_id: int | None) -> tuple[str, int]:
    """
    POST `question` (and `conversation_id`, if this continues an existing
    conversation) to the FastAPI backend's /ask endpoint. Returns
    (answer, conversation_id) — pass that conversation_id into the next
    call to keep the same conversation going; the backend creates a new
    one and hands back its id when conversation_id is None.

    Raises RuntimeError with a clean message on any failure — connection
    issues, a non-2xx response, or an unexpected response shape — so the
    caller (the UI code below) has exactly one exception type to handle,
    regardless of what actually went wrong underneath.
    """
    try:
        response = requests.post(API_URL,
                                  json={"question": question, "conversation_id": conversation_id},
                                    timeout=120)
        response.raise_for_status()
        data = response.json()
        return data["answer"], data["conversation_id"]
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

if "history" not in st.session_state:
    st.session_state.history = []

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
st.session_state.question = st.text_input("Ask a question about NBA player projections:")
if st.button("Submit"):
    if not st.session_state.question:
        st.warning("Please enter a question.")
    else:
        with st.spinner("Getting answer..."):
            try:
                answer, st.session_state.conversation_id = ask_backend(st.session_state.question, st.session_state.conversation_id)
                st.session_state.history.append((st.session_state.question, answer))
            except RuntimeError as e:
                st.error(str(e))
if st.button("New conversation"):
    st.session_state.conversation_id = None
    st.session_state.history = []
for question, answer in reversed(st.session_state.history):
    st.write(f"**Q:** {question}")
    st.write(f"**A:** {answer}")
