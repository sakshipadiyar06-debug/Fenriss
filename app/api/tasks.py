from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.models import Task, User
from app.services import task_service as ts

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    priority: str = "medium"
    deadline: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=1440)
    category: str = Field(default="general", max_length=50)
    recurring: bool = False
    recurrence_rule: str | None = None
    goal_id: int | None = None
    depends_on_id: int | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None
    deadline: datetime | None = None
    estimated_minutes: int | None = None
    actual_minutes: int | None = None
    category: str | None = None
    goal_id: int | None = None
    depends_on_id: int | None = None


def _get(db, task_id):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.get("")
def list_tasks(status: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Task).order_by(Task.created_at.desc())
    if status:
        q = q.where(Task.status == status)
    return [ts.task_dict(t) for t in db.scalars(q)]


@router.post("", status_code=201)
def create(body: TaskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        return ts.task_dict(ts.create_task(db, body.title, **body.model_dump(exclude={"title"})))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.patch("/{task_id}")
def patch(task_id: int, body: TaskPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _get(db, task_id)
    try:
        return ts.task_dict(ts.update_task(db, t, **body.model_dump(exclude_unset=True)))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/{task_id}")
def delete(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.delete(_get(db, task_id))
    db.commit()
    return {"deleted": task_id}
