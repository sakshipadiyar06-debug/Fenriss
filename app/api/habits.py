from datetime import date as Date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.core.timeutil import get_tz, local_today
from app.models import Course, HabitLog, LeetCodeEntry, User
from app.services import scoring_service as sc
from app.services import workflow_service as wf

router = APIRouter(prefix="/api/habits", tags=["habits"])


class LogIn(BaseModel):
    habit: str  # course | leetcode | planning | review
    minutes: int = Field(default=0, ge=0, le=1440)
    topic: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    date: Date | None = None


class CourseIn(BaseModel):
    name: str
    url: str = ""
    current_topic: str = ""
    progress_pct: float = 0
    start_date: Date | None = None
    target_date: Date | None = None
    notes: str = ""


class LeetIn(BaseModel):
    problem_name: str = ""
    problem_url: str = ""
    difficulty: str = "Medium"
    topic: str = "Arrays"
    status: str = "solved"
    time_spent: int = Field(default=30, ge=0, le=1440)
    notes: str = ""


@router.get("")
def habits(user: User = Depends(current_user), db: Session = Depends(get_db)):
    today = local_today(get_tz(user.timezone))
    c = db.scalar(select(Course).where(Course.active == True))  # noqa: E712
    return {"habits": {h: sc.habit_stats(db, h, today) for h in sc.HABITS},
            "course": {k: getattr(c, k) for k in ("id", "name", "url", "current_topic", "progress_pct", "start_date", "target_date", "notes")} if c else None,
            "course_trend": sc.course_trend(db, today), "leetcode": sc.leetcode_analysis(db)}


@router.post("", status_code=201)
def log(body: LogIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.habit not in sc.HABITS:
        raise HTTPException(422, f"habit must be one of {sc.HABITS}")
    wf.log_habit(db, user, body.habit, body.minutes, body.topic, body.notes, body.date)
    return sc.habit_stats(db, body.habit, local_today(get_tz(user.timezone)))


@router.put("/course")
def set_course(body: CourseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for old in db.scalars(select(Course).where(Course.active == True)):  # noqa: E712
        old.active = False
    c = Course(**body.model_dump(), active=True)
    db.add(c)
    user.current_course = body.name
    db.commit()
    return {"id": c.id, "name": c.name}


@router.post("/leetcode", status_code=201)
def add_leetcode(body: LeetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.difficulty not in ("Easy", "Medium", "Hard"):
        raise HTTPException(422, "difficulty must be Easy, Medium or Hard")
    e = wf.log_leetcode(db, user, body.time_spent, **body.model_dump(exclude={"time_spent"}))
    return {"id": e.id, "stats": sc.habit_stats(db, "leetcode", local_today(get_tz(user.timezone)))}


@router.get("/leetcode")
def leetcode(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LeetCodeEntry).order_by(LeetCodeEntry.id.desc()).limit(50)).all()
    return {"analysis": sc.leetcode_analysis(db), "recent": [{c.name: getattr(r, c.name) for c in LeetCodeEntry.__table__.columns} for r in rows]}
