"""
routers/chat.py — the conversational agent: /ask and conversation history.
"""

import logging

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, Request

import nba_projection_bot.agent as agent
import nba_projection_bot.db as db
from nba_projection_bot.dependencies import enforce_rate_limit, get_current_user_id, limiter
from nba_projection_bot.schemas import (
    AskRequest,
    AskResponse,
    ConversationHistoryResponse,
    ConversationMessage,
    ConversationSummary,
)

router = APIRouter()


@router.get("/conversations", response_model=list[ConversationSummary])
@limiter.limit("5/minute")
async def get_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: int = Depends(get_current_user_id),
) -> list[dict]:
    try:
        conversations = await db.list_conversations(user_id, limit=limit, offset=offset)
    except Exception as e:
        logging.exception("Unexpected error in GET /conversations")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return conversations


@router.get("/conversations/{conversation_id}", response_model=ConversationHistoryResponse)
@limiter.limit("5/minute")
async def get_conversation(
    request: Request,
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
) -> ConversationHistoryResponse:
    try:
        messages = await db.load_history(conversation_id, user_id)
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail="You don't have access to this conversation."
        ) from e
    except Exception as e:
        logging.exception(f"Unexpected error in GET /conversations/{conversation_id}")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e
    return ConversationHistoryResponse(messages=[ConversationMessage(**m) for m in messages])


@router.post("/ask", response_model=AskResponse)
@limiter.limit("5/minute")
async def ask(
    request: Request,
    ask_request: AskRequest,
    user_id: int = Depends(get_current_user_id),
    _: None = Depends(enforce_rate_limit),
) -> AskResponse:
    try:
        answer, conversation_id, projections, news = await agent.run_agent(
            ask_request.question, user_id, ask_request.conversation_id
        )
    except PermissionError as e:
        # Deliberately generic: don't confirm/deny whether conversation_id
        # exists at all
        raise HTTPException(
            status_code=403, detail="You don't have access to this conversation."
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except anthropic.APIError as e:
        logging.exception("Anthropic API error in /ask")
        raise HTTPException(
            status_code=502,
            detail="The AI service is temporarily unavailable. Please try again later.",
        ) from e
    except Exception as e:
        logging.exception("Unexpected error in /ask")
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later.",
        ) from e

    return AskResponse(
        answer=answer, conversation_id=conversation_id, projections=projections, news=news
    )
