from datetime import date, datetime
from typing import Optional
from sqlalchemy import JSON, Boolean, Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.tenancy import TenantMixin
from app.core.timeutil import utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    name: Mapped[str] = mapped_column(String(100), default="there")
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    wake_time: Mapped[str] = mapped_column(String(5), default="07:00")
    sleep_time: Mapped[str] = mapped_column(String(5), default="23:00")
    daily_hours: Mapped[float] = mapped_column(Float, default=6.0)
    goals_text: Mapped[str] = mapped_column(Text, default="")
    current_course: Mapped[str] = mapped_column(String(200), default="")
    interests: Mapped[str] = mapped_column(Text, default="")
    news_interests: Mapped[str] = mapped_column(Text, default="")
    # reminder times live in the DB so they can be changed without a restart
    morning_time: Mapped[str] = mapped_column(String(5), default="08:00")
    course_time: Mapped[str] = mapped_column(String(5), default="18:00")
    leetcode_time: Mapped[str] = mapped_column(String(5), default="19:00")
    evening_time: Mapped[str] = mapped_column(String(5), default="21:30")
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Goal(TenantMixin, Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    milestones: Mapped[list] = mapped_column(JSON, default=list)  # [{"title":..., "done":bool}]
    metric: Mapped[str] = mapped_column(String(30), default="manual")  # manual | leetcode_solved | course_progress
    target_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Memory(TenantMixin, Base):
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChatMessage(TenantMixin, Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(TenantMixin, Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # e.g. reminder:course, morning, evening
    message: Mapped[str] = mapped_column(Text)
    date: Mapped[date] = mapped_column(Date)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    """Server-side login session. Only a SHA-256 of the cookie token is stored."""
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
