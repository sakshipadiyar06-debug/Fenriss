from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "Fenris"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    database_url: str = "sqlite:///./data/assistant.db"
    timezone: str = "Asia/Kolkata"
    morning_briefing_time: str = "08:00"
    course_reminder_time: str = "18:00"
    leetcode_reminder_time: str = "19:00"
    evening_review_time: str = "21:30"
    mandatory_minutes: int = 30
    news_refresh_hours: int = 3
    enable_scheduler: bool = True
    allow_registration: bool = True
    cookie_secure: bool = False  # set true when served over HTTPS (also auto-detected)
    session_days: int = 30
    ai_daily_limit: int = 40  # AI-assisted messages per user per day (protects your OpenAI bill)
    enable_docs: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
