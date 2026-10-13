from datetime import date, datetime
from typing import Optional
from sqlalchemy import JSON, Date, DateTime, Float, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.tenancy import TenantMixin
from app.core.timeutil import utcnow


class DailyReview(TenantMixin, Base):
    __tablename__ = "daily_reviews"
    __table_args__ = (UniqueConstraint("user_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date)
    score: Mapped[float] = mapped_column(Float, default=0)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    answers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WeeklyReview(TenantMixin, Base):
    __tablename__ = "weekly_reviews"
    __table_args__ = (UniqueConstraint("user_id", "week_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
