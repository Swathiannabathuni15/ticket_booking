"""POST /agent/chat - the booking assistant (see app/services/agent_service.py)."""

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit_deps import limit_agent_per_user
from app.redis_client import get_redis
from app.services import agent_service as svc

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ChatOut(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatOut, dependencies=[Depends(limit_agent_per_user)])
def chat(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    r: redis.Redis = Depends(get_redis),
    llm: svc.LLMClient = Depends(svc.get_llm),
):
    try:
        return ChatOut(reply=svc.chat(db, r, user, llm, body.message))
    except svc.AgentUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
