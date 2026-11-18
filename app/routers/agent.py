"""POST /agent/chat - the booking assistant (see app/services/agent_service.py)."""

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit_deps import _too_many_requests
from app.redis_client import get_redis
from app.services import agent_service as svc
from app.services import rate_limit

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ChatOut(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatOut)
def chat(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
    llm: svc.LLMClient = Depends(svc.get_llm),
):
    try:
        rate_limit.check(
            r, f"agent:{user.id}", settings.rate_limit_agent_per_window, settings.rate_limit_window_seconds
        )
    except rate_limit.RateLimitExceeded as exc:
        _too_many_requests(exc.retry_after_seconds)
    try:
        return ChatOut(reply=svc.chat(db, r, user, llm, body.message))
    except svc.AgentUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
