"""Task CRUD, intelligent prioritisation and procrastination detection."""
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher
from sqlalchemy import select
from app.core.timeutil import utcnow
from app.models import PRIORITIES, STATUSES, Task

PRIORITY_WEIGHT = {"critical": 40, "high": 30, "medium": 15, "low": 5}
OPEN = ("pending", "in_progress")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WD_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thurs": 3, "fri": 4}


def task_dict(t: Task) -> dict:
    return {
        "id": t.id, "title": t.title, "description": t.description, "priority": t.priority,
        "status": t.status, "deadline": t.deadline.isoformat() if t.deadline else None,
        "estimated_minutes": t.estimated_minutes, "actual_minutes": t.actual_minutes,
        "category": t.category, "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "recurring": t.recurring, "recurrence_rule": t.recurrence_rule,
        "postponement_count": t.postponement_count, "goal_id": t.goal_id, "depends_on_id": t.depends_on_id,
    }


def end_of_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59))


def parse_deadline_text(text: str, today: date) -> datetime | None:
    """Rule-based relative date parsing (used when the LLM is unavailable). Returns end of that day."""
    low = (text or "").lower()
    if m := re.search(r"\b(\d{4}-\d{2}-\d{2})\b", low):
        return end_of_day(date.fromisoformat(m.group(1)))
    if "day after tomorrow" in low:
        return end_of_day(today + timedelta(days=2))
    if re.search(r"\b(tomorrow|tmrw)\b", low):
        return end_of_day(today + timedelta(days=1))
    if re.search(r"\b(today|tonight)\b", low):
        return end_of_day(today)
    if m := re.search(r"\bin (\d+) days?\b", low):
        return end_of_day(today + timedelta(days=int(m.group(1))))
    if "next week" in low:
        return end_of_day(today + timedelta(days=7 - today.weekday()))
    if "this week" in low or "this weekend" in low:
        return end_of_day(today + timedelta(days=(4 - today.weekday()) % 7 or 0))
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", low):
            return end_of_day(today + timedelta(days=(i - today.weekday()) % 7 or 7))
    for ab, i in WD_ABBR.items():
        if re.search(rf"\b{ab}\b", low):
            return end_of_day(today + timedelta(days=(i - today.weekday()) % 7 or 7))
    return None


def create_task(db, title: str, **kw) -> Task:
    priority = kw.get("priority") or "medium"
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    status = kw.get("status") or "pending"
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    fields = {k: v for k, v in kw.items() if k in {
        "description", "deadline", "estimated_minutes", "category", "recurring",
        "recurrence_rule", "goal_id", "depends_on_id"} and v is not None}
    t = Task(title=title.strip(), priority=priority, status=status, **fields)
    if status == "completed":
        t.completed_at = utcnow()
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def update_task(db, t: Task, **ch) -> Task:
    if "priority" in ch and ch["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if "status" in ch and ch["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    new_dl = ch.get("deadline")
    if new_dl and t.deadline and new_dl > t.deadline:
        t.postponement_count += 1  # moved later = postponed
    if ch.get("status") == "deferred" and t.status != "deferred":
        t.postponement_count += 1
    old_status = t.status
    for k, v in ch.items():
        setattr(t, k, v)
    if t.status == "completed" and old_status != "completed":
        t.completed_at = utcnow()
    elif t.status != "completed":
        t.completed_at = None
    t.updated_at = utcnow()
    db.commit()
    if t.status == "completed" and old_status != "completed" and t.recurring and t.recurrence_rule:
        _spawn_next(db, t)
    return t


def complete_task(db, t: Task, actual_minutes: int | None = None) -> Task:
    ch = {"status": "completed"}
    if actual_minutes:
        ch["actual_minutes"] = actual_minutes
    return update_task(db, t, **ch)


def _spawn_next(db, t: Task):
    step = {"daily": 1, "weekdays": 1, "weekly": 7}.get(t.recurrence_rule or "", None)
    if not step:
        return
    nd = None
    if t.deadline:
        nd = t.deadline + timedelta(days=step)
        while t.recurrence_rule == "weekdays" and nd.weekday() >= 5:
            nd += timedelta(days=1)
    create_task(db, t.title, description=t.description, priority=t.priority, deadline=nd,
                estimated_minutes=t.estimated_minutes, category=t.category, recurring=True,
                recurrence_rule=t.recurrence_rule, goal_id=t.goal_id)


def postpone(db, t: Task, new_deadline: datetime) -> Task:
    return update_task(db, t, deadline=new_deadline, status="pending")


# ---------------------------------------------------------------- prioritisation
def urgency_score(t: Task, now: datetime, dependents: int = 0, blocked: bool = False) -> float:
    """Deadline + importance + consequences + effort + dependencies + goals."""
    s = float(PRIORITY_WEIGHT.get(t.priority, 15))
    if t.deadline:
        days = (t.deadline.date() - now.date()).days
        if t.deadline < now:
            s += 40
        elif days == 0:
            s += 35
        elif days == 1:
            s += 25
        elif days <= 3:
            s += 15
        elif days <= 7:
            s += 8
    if t.goal_id:
        s += 8  # contributes to a long-term goal
    if dependents:
        s += 8  # other tasks wait on this one
    s += min(t.postponement_count, 5) * 2  # stale tasks need a decision
    est = t.estimated_minutes or 30
    s += 3 if est <= 30 else (-3 if est >= 180 else 0)  # quick wins vs. huge blocks
    if blocked:
        s -= 50
    return s


def rank_tasks(db, now: datetime) -> list[tuple[Task, float, bool]]:
    tasks = db.scalars(select(Task).where(Task.status.in_(OPEN))).all()
    open_ids = {t.id for t in tasks}
    deps = Counter(t.depends_on_id for t in tasks if t.depends_on_id in open_ids)
    out = []
    for t in tasks:
        blocked = t.depends_on_id in open_ids
        out.append((t, urgency_score(t, now, deps[t.id], blocked), blocked))
    out.sort(key=lambda x: -x[1])
    return out


def classify(ranked, max_must: int = 3) -> dict:
    """Must (max 3, up to 5 for truly critical) / Should / Nice. Never everything is a priority."""
    must, should, nice = [], [], []
    for t, score, blocked in ranked:
        if not blocked and score >= 65 and (len(must) < max_must or (score >= 80 and len(must) < 5)):
            must.append((t, score))
        elif not blocked and score >= 30:
            should.append((t, score))
        else:
            nice.append((t, score))
    return {"must": must, "should": should, "nice": nice}


def find_task(db, ref: str) -> Task | None:
    ref = (ref or "").lower().strip()
    if not ref:
        return None
    best, best_s = None, 0.0
    toks = [w for w in re.findall(r"\w+", ref) if w not in {"the", "my", "a", "an", "task", "to"}]
    for t in db.scalars(select(Task).where(Task.status.in_(OPEN + ("deferred",)))).all():
        title = t.title.lower()
        s = SequenceMatcher(None, ref, title).ratio()
        if toks and all(w in title for w in toks):
            s = max(s, 0.9)
        if s > best_s:
            best, best_s = t, s
    return best if best_s >= 0.5 else None


def unfinished_review(db, today: date, now: datetime) -> list[dict]:
    """Evaluate (never auto-move) open tasks whose deadline has passed."""
    out = []
    for t in db.scalars(select(Task).where(Task.status.in_(OPEN), Task.deadline.is_not(None))).all():
        if t.deadline.date() >= today:
            continue
        if t.postponement_count >= 3:
            rec, why = "decide", f"Postponed {t.postponement_count} times. Schedule a specific block or drop it."
        elif t.priority in ("critical", "high"):
            rec, why = "keep", "Still important and overdue. Reschedule it first."
        else:
            rec, why = "reconsider", "Low consequence. Move it only if it still matters."
        out.append({"id": t.id, "title": t.title, "priority": t.priority, "recommendation": rec, "reason": why,
                    "days_overdue": (today - t.deadline.date()).days})
    return out


def procrastination(db, min_count: int = 2) -> list[dict]:
    rows = db.scalars(select(Task).where(Task.status.in_(OPEN + ("deferred",)), Task.postponement_count >= min_count)
                      .order_by(Task.postponement_count.desc())).all()
    out = []
    for t in rows:
        msg = (f"You've postponed this {t.postponement_count} times. We should either schedule a specific "
               "30-minute block or reconsider whether this task is actually important."
               if t.postponement_count >= 3 else f"Postponed {t.postponement_count} times. Watch this one.")
        out.append({"id": t.id, "title": t.title, "postponed": t.postponement_count, "message": msg})
    return out
