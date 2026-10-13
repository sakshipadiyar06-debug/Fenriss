from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.core.timeutil import get_tz, local_today
from app.models import DailyReview, User
from app.services import scoring_service as sc
from app.services import workflow_service as wf

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


@router.get("/daily")
def daily(day: date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tz = get_tz(user.timezone)
    d = day or local_today(tz)
    rev = db.scalar(select(DailyReview).where(DailyReview.date == d))
    return {"date": d.isoformat(), "live_score": sc.daily_score(db, d, tz), "saved": {"text": rev.text, "answers": rev.answers} if rev else None}


@router.get("/weekly")
def weekly(refresh: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return wf.weekly(db, user, force=refresh)
