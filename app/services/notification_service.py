"""In-app notifications (stored in DB + logged). One reminder per habit per day, adaptive timing."""
import logging
from datetime import timedelta
from sqlalchemy import select
from app.core.timeutil import get_tz, local_today
from app.models import Notification, User
from app.services import scoring_service as sc

log = logging.getLogger("fenris.notify")


def notify(db, kind: str, message: str, day) -> Notification:
    n = Notification(kind=kind, message=message, date=day)
    db.add(n)
    db.commit()
    log.info("Notification [%s] created", kind)
    return n


def reminder_for(db, user: User, habit: str) -> str | None:
    """Return the reminder text (and store it) or None when no reminder is due."""
    tz = get_tz(user.timezone)
    today = local_today(tz)
    if sc.met_set(db, habit, today, today):
        return None  # already done: stay quiet
    kind = f"reminder:{habit}"
    if db.scalar(select(Notification.id).where(Notification.kind == kind, Notification.date == today)):
        return None  # never more than one reminder a day
    ignored = 0  # reminders sent on days the habit was then not done
    for n in db.scalars(select(Notification).where(Notification.kind == kind, Notification.date >= today - timedelta(days=7))):
        if n.date not in sc.met_set(db, habit, n.date, n.date):
            ignored += 1
    label = "Course" if habit == "course" else "LeetCode"
    msg = f"{label}: {sc.target_for(habit)} minutes still to do today."
    hour = sc.patterns(db, tz, today, 28)["typical_habit_hour"].get(habit)
    if ignored >= 3 and hour is not None:
        msg = f"You normally complete {label} around {hour}:00. Want to move today's session there?"
    notify(db, kind, msg, today)
    return msg
