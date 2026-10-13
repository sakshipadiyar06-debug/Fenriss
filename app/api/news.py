from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.core.timeutil import utcnow
from app.models import NewsItem, User
from app.services import news_service as ns

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("")
def briefing(user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        return ns.get_briefing(db, user.news_interests)
    except Exception:
        return {"available": False, "message": "News service temporarily unavailable.", "top": [], "categories": {}}


@router.post("/refresh")
def refresh(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """News is shared by all users, so refreshes are throttled to one every 10 minutes."""
    last = ns.LAST.get("last_run")
    if last and utcnow() - datetime.fromisoformat(last) < timedelta(minutes=10):
        return dict(ns.LAST, throttled=True)
    try:
        return ns.collect_news(db)
    except Exception:
        raise HTTPException(503, "News service temporarily unavailable.")


@router.get("/{news_id}")
def item(news_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    i = db.get(NewsItem, news_id)
    if not i:
        raise HTTPException(404, "Not found")
    return ns.news_dict(i)
