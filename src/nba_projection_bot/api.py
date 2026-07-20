"""
api.py — Stage 5: expose the agent as an HTTP API.
"""

import anthropic
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import nba_projection_bot.agent as agent

# Rate limiting by client IP — /ask triggers real, billed Anthropic API
# calls (possibly several, per the agent's tool-use loop), so this caps
# how fast any one client can spend your API budget.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

class AskRequest(BaseModel):
    question: str = Field(max_length=500)

class AskResponse(BaseModel):
    answer: str

@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
@limiter.limit("5/minute")
async def ask(request: Request, ask_request: AskRequest) -> AskResponse:
    try:
        answer = await agent.run_agent(ask_request.question)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except anthropic.APIError:
        raise HTTPException(
            status_code=502,
            detail="The AI service is temporarily unavailable. Please try again later.",
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        )

    return AskResponse(answer=answer)


if __name__ == "__main__":
    # Stage 5 checkpoint — this file isn't run directly like the others.
    # Instead, from src/, run:
    #     uvicorn nba_projection_bot.api:app --reload
    # Then open http://127.0.0.1:8000/docs to see the auto-generated API
    # docs and try the /ask endpoint interactively — same as you did with
    # the reference file's /notes endpoint, but this one triggers a real
    # (billed) call through run_agent().
    pass
