from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.models import User
from app.services import workflow_service as wf

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class EveningIn(BaseModel):
    answers: dict | None = None


@router.post("/chat")
def chat(body: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return wf.handle_message(db, user, body.message)


@router.post("/morning")
def morning(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return wf.morning(db, user)


@router.post("/evening")
def evening(body: EveningIn | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    answers = body.answers if body else None
    if answers and len(str(answers)) > 4000:
        answers = {k: str(v)[:500] for k, v in list(answers.items())[:10]}
    return wf.evening(db, user, answers)
