"""
schemas.py — Pydantic request/response models for api.py's routes.
"""

import datetime

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(max_length=500)
    conversation_id: int | None = None


class AskResponse(BaseModel):
    answer: str
    conversation_id: int
    projections: list[dict] = []
    news: list[dict] = []


class ConversationSummary(BaseModel):
    id: int
    title: str | None
    created_at: datetime.datetime


class ConversationMessage(BaseModel):
    role: str
    content: str
    projections: list[dict] = []
    news: list[dict] = []


class ConversationHistoryResponse(BaseModel):
    messages: list[ConversationMessage]


class DeepAnalysisRequest(BaseModel):
    player_name: str


class DeepAnalysisResponse(BaseModel):
    status: str
    result: str | None
    error: str | None
    produced: bool
    created_at: datetime.datetime


class DeepAnalysisJobSummary(BaseModel):
    id: int
    player_name: str
    status: str
    produced: bool
    created_at: datetime.datetime
