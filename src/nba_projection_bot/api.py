"""
api.py — Stage 5: expose the agent as an HTTP API.
"""

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import nba_projection_bot.agent as agent

app = FastAPI()

class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str

@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    try:
        answer = agent.run_agent(request.question)
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
