from datetime import datetime
from typing import Optional
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.timeutil import utcnow


class NewsItem(Base):
    __tablename__ = "news_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    source: Mapped[str] = mapped_column(String(200))  # original publisher where known
    source_tier: Mapped[int] = mapped_column(Integer, default=3)
    category: Mapped[str] = mapped_column(String(30), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    summary: Mapped[str] = mapped_column(Text, default="")
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    via_aggregator: Mapped[bool] = mapped_column(Boolean, default=False)
    corroboration: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # all sources reporting the story
    briefing: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # cached AI analysis
