from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.tenancy import TenantMixin
from app.core.timeutil import utcnow

STATUSES = ("pending", "in_progress", "completed", "cancelled", "deferred")
PRIORITIES = ("critical", "high", "medium", "low")


class Task(TenantMixin, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="medium")
    status: Mapped[str] = mapped_column(String(15), default="pending", index=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # naive local time
    estimated_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actual_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(50), default="general")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    recurrence_rule: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # daily|weekdays|weekly
    postponement_count: Mapped[int] = mapped_column(Integer, default=0)
    goal_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    depends_on_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
