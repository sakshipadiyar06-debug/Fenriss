from datetime import date
from fastapi import APIRouter, Depends, HTTPException
import re
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo
from app.core.database import get_db
from app.core.security import current_user
from app.models import Course, Goal, Memory, Notification, User
from app.services import workflow_service as wf

router = APIRouter(prefix="/api", tags=["profile"])


HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class SetupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    timezone: str = Field(default="Asia/Kolkata", max_length=60)
    wake_time: str = Field(default="07:00", pattern=HHMM)
    sleep_time: str = Field(default="23:00", pattern=HHMM)
    daily_hours: float = Field(default=6, gt=0, le=18)
    current_course: str = Field(default="", max_length=200)
    goals: str = Field(default="", max_length=500)
    interests: str = Field(default="", max_length=500)
    news_interests: str = Field(default="", max_length=500)
    morning_time: str | None = Field(default=None, pattern=HHMM)
    course_time: str | None = Field(default=None, pattern=HHMM)
    leetcode_time: str | None = Field(default=None, pattern=HHMM)
    evening_time: str | None = Field(default=None, pattern=HHMM)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    target_date: date | None = None
    milestones: list[str] = Field(default=[], max_length=20)
    metric: str = "manual"
    target_value: float | None = None


@router.get("/setup")
def get_setup(u: User = Depends(current_user)):
    hidden = {"password_hash", "created_at", "is_active"}  # never expose credentials
    return {c.name: getattr(u, c.name) for c in u.__table__.columns if c.name not in hidden}


@router.post("/setup")
def setup(body: SetupIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(422, "Unknown timezone (use e.g. Asia/Kolkata)")
    for k in ("name", "timezone", "wake_time", "sleep_time", "daily_hours", "current_course", "interests", "news_interests"):
        setattr(u, k, getattr(body, k))
    for k in ("morning_time", "course_time", "leetcode_time", "evening_time"):
        if getattr(body, k):
            setattr(u, k, getattr(body, k))
    u.goals_text = body.goals
    if body.current_course and not db.scalar(select(Course).where(Course.active == True)):  # noqa: E712
        db.add(Course(name=body.current_course, start_date=date.today()))
    if body.goals and not db.scalar(select(Goal)):
        db.add(Goal(title=body.goals[:200]))
    u.setup_complete = True
    db.commit()
    return {"ok": True}


@router.get("/goals")
def goals(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return wf.goals_progress(db)


@router.post("/goals", status_code=201)
def add_goal(body: GoalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = Goal(title=body.title, description=body.description, target_date=body.target_date, metric=body.metric,
             target_value=body.target_value, milestones=[{"title": m, "done": False} for m in body.milestones])
    db.add(g)
    db.commit()
    return {"id": g.id}


@router.post("/goals/{gid}/milestones/{idx}")
def toggle_milestone(gid: int, idx: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = db.get(Goal, gid)
    if not g or idx >= len(g.milestones or []):
        raise HTTPException(404, "Not found")
    ms = [dict(m) for m in g.milestones]
    ms[idx]["done"] = not ms[idx]["done"]
    g.milestones = ms
    db.commit()
    return {"ok": True}


@router.get("/memory")
def memories(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": m.id, "content": m.content, "created_at": m.created_at.isoformat()} for m in db.scalars(select(Memory).order_by(Memory.id.desc()))]


@router.delete("/memory/{mid}")
def del_memory(mid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.get(Memory, mid)
    if not m:
        raise HTTPException(404, "Not found")
    db.delete(m)
    db.commit()
    return {"deleted": mid}


@router.post("/notifications/read")
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.read == False)):  # noqa: E712
        n.read = True
    db.commit()
    return {"ok": True}
