from datetime import date, datetime
from typing import Optional
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.tenancy import TenantMixin
from app.core.timeutil import utcnow


class HabitLog(TenantMixin, Base):
    """One row per logged session. habit: course | leetcode | planning | review."""
    __tablename__ = "habit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    habit: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    topic: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Course(TenantMixin, Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500), default="")
    current_topic: Mapped[str] = mapped_column(String(200), default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LeetCodeEntry(TenantMixin, Base):
    __tablename__ = "leetcode_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    problem_name: Mapped[str] = mapped_column(String(200), default="")
    problem_url: Mapped[str] = mapped_column(String(500), default="")
    difficulty: Mapped[str] = mapped_column(String(10), default="Medium")  # Easy|Medium|Hard
    topic: Mapped[str] = mapped_column(String(50), default="Arrays")
    status: Mapped[str] = mapped_column(String(15), default="solved")  # solved|attempted|stuck
    time_spent: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
