"""Scheduler. One minute-tick checks every user's own times/timezone, so reminders are per-user and
changing a time in Settings takes effect immediately. News collection is shared by all users."""
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from app.core.database import SessionLocal
from app.core.timeutil import get_tz, local_now, parse_hhmm
from app.models import User
from app.services import news_service as ns
from app.services import notification_service as nt
from app.services import workflow_service as wf

log = logging.getLogger("fenris.scheduler")
scheduler = BackgroundScheduler()
_done: set = set()  # (user_id, job, date) already executed
CATCH_UP = timedelta(minutes=10)  # still run a job if the tick/restart was this late


def morning_job(db, user):
    r = wf.morning(db, user)
    nt.notify(db, "morning", r["text"], local_now(get_tz(user.timezone)).date())


def course_job(db, user):
    nt.reminder_for(db, user, "course")


def leetcode_job(db, user):
    nt.reminder_for(db, user, "leetcode")


def evening_job(db, user):
    nt.notify(db, "evening", "Time for your evening review. Say 'End my day' to see today's score and plan tomorrow.",
              local_now(get_tz(user.timezone)).date())


def weekly_job(db, user):
    wf.weekly(db, user)


JOBS = {"morning": morning_job, "course": course_job, "leetcode": leetcode_job, "evening": evening_job, "weekly": weekly_job}


def run_for_user(user_id: int, job: str):
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user and user.is_active:
            db.info["uid"] = user.id  # row-level isolation applies to scheduled work too
            JOBS[job](db, user)
    except Exception:
        log.exception("Scheduled job %s failed", job)
    finally:
        db.close()


def due_jobs(user: User, now: datetime) -> list[str]:
    out = []
    for jid, t in (("morning", user.morning_time), ("course", user.course_time),
                   ("leetcode", user.leetcode_time), ("evening", user.evening_time)):
        at = datetime.combine(now.date(), parse_hhmm(t))
        if timedelta(0) <= now - at < CATCH_UP:
            out.append(jid)
    if now.weekday() == 6:
        at = datetime.combine(now.date(), parse_hhmm("21:45"))
        if timedelta(0) <= now - at < CATCH_UP:
            out.append("weekly")
    return out


def tick():
    try:
        db = SessionLocal()
        try:
            users = db.scalars(select(User).where(User.setup_complete == True, User.is_active == True)).all()  # noqa: E712
        finally:
            db.close()
        for u in users:
            now = local_now(get_tz(u.timezone))
            for jid in due_jobs(u, now):
                key = (u.id, jid, now.date())
                if key not in _done:
                    _done.add(key)
                    run_for_user(u.id, jid)
        today = datetime.utcnow().date() - timedelta(days=2)
        for k in [k for k in _done if k[2] < today]:
            _done.discard(k)
    except Exception:
        log.exception("Scheduler tick failed")


def news_job():
    db = SessionLocal()
    try:
        ns.collect_news(db)
    except Exception:
        log.exception("News job failed")
    finally:
        db.close()


def start():
    if not scheduler.running:
        scheduler.start()
    scheduler.add_job(tick, "interval", seconds=60, id="tick", max_instances=1, coalesce=True, replace_existing=True)
    scheduler.add_job(news_job, "interval", hours=3, id="news", max_instances=1, replace_existing=True)
    scheduler.add_job(news_job, "date", id="news_boot", replace_existing=True)
