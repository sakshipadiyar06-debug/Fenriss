"""Habit stats, streaks, daily/weekly scores, LeetCode and pattern analytics."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from sqlalchemy import and_, func, or_, select
from app.core.config import get_settings
from app.core.timeutil import day_bounds_utc, to_local
from app.models import Course, HabitLog, LeetCodeEntry, Task
from app.services.task_service import PRIORITY_WEIGHT

WEIGHTS = {"productivity": .30, "course": .20, "leetcode": .20, "consistency": .15,
           "planning": .10, "personal_development": .05}
MANDATORY = ("course", "leetcode")
HABITS = ("course", "leetcode", "planning", "review")
GROWTH_CATEGORIES = {"learning", "health", "personal", "fitness", "study"}
LEETCODE_TOPICS = ["Arrays", "Strings", "Hash Maps", "Two Pointers", "Sliding Window", "Binary Search", "Stack",
                   "Queue", "Linked List", "Trees", "Graphs", "Dynamic Programming", "Greedy", "Backtracking",
                   "Heap", "Intervals", "Math", "Other"]


def target_for(habit: str) -> int:
    return get_settings().mandatory_minutes if habit in MANDATORY else 0


def compute_streak(met: set, today: date) -> int:
    """Consecutive met days. Today not being met *yet* doesn't break a streak."""
    cur = today if today in met else today - timedelta(days=1)
    n = 0
    while cur in met:
        n += 1
        cur -= timedelta(days=1)
    return n


def habit_days(db, habit: str, start: date, end: date) -> dict:
    rows = db.execute(select(HabitLog.date, func.coalesce(func.sum(HabitLog.minutes), 0), func.count())
                      .where(HabitLog.habit == habit, HabitLog.date >= start, HabitLog.date <= end)
                      .group_by(HabitLog.date)).all()
    return {d: (int(m), c) for d, m, c in rows}


def met_set(db, habit: str, start: date, end: date) -> set:
    tgt = target_for(habit)
    return {d for d, (m, c) in habit_days(db, habit, start, end).items() if (m >= tgt if tgt else c > 0)}


def minutes_on(db, habit: str, d: date) -> int:
    return habit_days(db, habit, d, d).get(d, (0, 0))[0]


def habit_stats(db, habit: str, today: date) -> dict:
    met = met_set(db, habit, today - timedelta(days=400), today)
    first = db.scalar(select(func.min(HabitLog.date)).where(HabitLog.habit == habit))
    start = max(first, today - timedelta(days=29)) if first else today
    missed = [start + timedelta(days=i) for i in range((today - start).days)]
    missed = [d for d in missed if d not in met]
    last7 = [today - timedelta(days=i) for i in range(7)]
    last30 = [today - timedelta(days=i) for i in range(30)]
    return {"habit": habit, "target_minutes": target_for(habit), "today_minutes": minutes_on(db, habit, today),
            "met_today": today in met, "streak": compute_streak(met, today),
            "week_done": sum(d in met for d in last7), "week_total": 7,
            "month_done": sum(d in met for d in last30), "month_total": 30,
            "missed_days_30": len(missed)}


# ---------------------------------------------------------------- daily score
def weighted_score(components: dict) -> int:
    return round(sum(components[k] * w for k, w in WEIGHTS.items()))


def productivity_score(db, d: date, tz) -> float:
    s, e = day_bounds_utc(d, tz)
    ds = datetime.combine(d, time.min)
    de = ds + timedelta(days=1)
    tasks = db.scalars(select(Task).where(Task.status != "cancelled", or_(
        and_(Task.deadline >= ds, Task.deadline < de), and_(Task.completed_at >= s, Task.completed_at < e)))).all()
    if not tasks:
        return 70.0  # nothing planned: neutral, so an empty day doesn't read as failure
    w = lambda t: PRIORITY_WEIGHT.get(t.priority, 15)
    return 100 * sum(w(t) for t in tasks if t.status == "completed") / sum(w(t) for t in tasks)


def daily_score(db, d: date, tz) -> dict:
    cm, lm = minutes_on(db, "course", d), minutes_on(db, "leetcode", d)
    tgt = get_settings().mandatory_minutes
    week = [d - timedelta(days=i) for i in range(7)]
    rate = (sum(x in met_set(db, h, week[-1], d) for h in MANDATORY for x in week)) / 14
    s, e = day_bounds_utc(d, tz)
    grow = sum((t.actual_minutes or t.estimated_minutes or 30) for t in db.scalars(select(Task).where(
        Task.status == "completed", Task.completed_at >= s, Task.completed_at < e)).all()
        if t.category in GROWTH_CATEGORIES)
    extra = max(0, cm + lm - 2 * tgt) + grow
    comps = {"productivity": productivity_score(db, d, tz), "course": min(100, 100 * cm / tgt),
             "leetcode": min(100, 100 * lm / tgt), "consistency": 100 * rate,
             "planning": 100.0 if d in met_set(db, "planning", d, d) else 0.0,
             "personal_development": min(100, 100 * extra / 30)}
    comps = {k: round(v) for k, v in comps.items()}
    return {"date": d.isoformat(), "score": weighted_score(comps), "components": comps}


# ---------------------------------------------------------------- patterns & analytics
def course_trend(db, today: date) -> dict:
    d7 = sum(m for m, _ in habit_days(db, "course", today - timedelta(days=6), today).values())
    p7 = sum(m for m, _ in habit_days(db, "course", today - timedelta(days=13), today - timedelta(days=7)).values())
    last = db.scalar(select(func.max(HabitLog.date)).where(HabitLog.habit == "course", HabitLog.minutes > 0))
    slowing = p7 > 0 and d7 < 0.7 * p7
    msg = None
    if slowing:
        msg = f"Course time dropped from {p7} to {d7} minutes week over week."
    elif last and (today - last).days >= 3:
        msg = f"No course time logged for {(today - last).days} days."
    return {"last_7": d7, "prev_7": p7, "slowing": bool(msg), "message": msg,
            "days_since_last": (today - last).days if last else None}


def leetcode_analysis(db, today: date | None = None) -> dict:
    rows = db.scalars(select(LeetCodeEntry)).all()
    by = defaultdict(list)
    for r in rows:
        by[r.topic].append(r)
    topics = {}
    for t, rs in by.items():
        solved = [r for r in rs if r.status == "solved"]
        rate = len(solved) / len(rs)
        avg = sum(r.time_spent for r in rs) / len(rs) if rs else 0
        if len(rs) < 2:
            rating = "not enough data"
        elif rate >= 0.75 and avg <= 35:
            rating = "strong"
        elif rate < 0.5:
            rating = "weak"
        else:
            rating = "medium"
        topics[t] = {"attempts": len(rs), "solved": len(solved), "avg_minutes": round(avg), "rating": rating}
    return {"total_solved": sum(r.status == "solved" for r in rows), "total_attempts": len(rows),
            "topics": topics, "untouched": [t for t in LEETCODE_TOPICS if t not in by and t != "Other"],
            "weak": [t for t, v in topics.items() if v["rating"] == "weak"]}


def patterns(db, tz, today: date, days: int = 28, daily_hours: float = 6.0) -> dict:
    start = today - timedelta(days=days)
    s, e = day_bounds_utc(start, tz)[0], day_bounds_utc(today, tz)[1]
    done = db.scalars(select(Task).where(Task.completed_at >= s, Task.completed_at < e)).all()
    hours = Counter(to_local(t.completed_at, tz).hour for t in done)
    logs = db.scalars(select(HabitLog).where(HabitLog.date >= start, HabitLog.habit.in_(MANDATORY))).all()
    habit_hours = defaultdict(list)
    for l in logs:
        habit_hours[l.habit].append(to_local(l.created_at, tz).hour)
    ratios = [t.actual_minutes / t.estimated_minutes for t in done if t.actual_minutes and t.estimated_minutes]
    factor = round(sum(ratios) / len(ratios), 2) if len(ratios) >= 5 else 1.0
    per_day = defaultdict(int)
    for t in db.scalars(select(Task).where(Task.deadline >= datetime.combine(start, time.min),
                                           Task.deadline < datetime.combine(today + timedelta(days=1), time.min),
                                           Task.status != "cancelled")).all():
        per_day[t.deadline.date()] += t.estimated_minutes or 30
    overloaded = sorted(d.isoformat() for d, m in per_day.items() if m > daily_hours * 60)
    now = datetime.combine(today, time(23, 59))
    missed = [t.title for t in db.scalars(select(Task).where(
        Task.deadline.is_not(None), Task.deadline >= datetime.combine(start, time.min), Task.deadline < now)).all()
        if t.status in ("pending", "in_progress", "deferred")
        or (t.completed_at and to_local(t.completed_at, tz) > t.deadline)]
    avoided = db.scalars(select(Task).where(Task.postponement_count >= 2).order_by(
        Task.postponement_count.desc()).limit(5)).all()
    typical = {h: Counter(v).most_common(1)[0][0] for h, v in habit_hours.items() if len(v) >= 3}
    return {"best_task_hours": [h for h, _ in hours.most_common(3)] if len(done) >= 5 else [],
            "typical_habit_hour": typical, "estimation_factor": factor, "overloaded_days": overloaded,
            "missed_deadlines": missed[:10], "avoided_tasks": [{"title": t.title, "postponed": t.postponement_count} for t in avoided],
            "sample_size": len(done)}


def week_summary(db, end: date, tz, daily_hours: float = 6.0) -> dict:
    start = end - timedelta(days=6)
    scores = [daily_score(db, start + timedelta(days=i), tz) for i in range(7)]
    s, e = day_bounds_utc(start, tz)[0], day_bounds_utc(end, tz)[1]
    completed = db.scalar(select(func.count()).select_from(Task).where(Task.completed_at >= s, Task.completed_at < e))
    postponed = db.scalar(select(func.coalesce(func.sum(Task.postponement_count), 0)).where(
        Task.updated_at >= s, Task.updated_at < e))
    habits = {}
    for h in HABITS:
        met = met_set(db, h, start, end)
        mins = sum(m for m, _ in habit_days(db, h, start, end).values())
        habits[h] = {"days": len([d for d in met if start <= d <= end]), "minutes": mins,
                     "streak": compute_streak(met_set(db, h, end - timedelta(days=400), end), end)}
    ranked = sorted(HABITS, key=lambda h: -habits[h]["days"])
    pat = patterns(db, tz, end, 7, daily_hours)
    if pat["overloaded_days"]:
        problem = "Too many tasks scheduled on " + ", ".join(pat["overloaded_days"][:3])
    elif (postponed or 0) >= 5:
        problem = "Tasks keep getting pushed back"
    elif habits[ranked[-1]]["days"] < 5:
        problem = f"Inconsistent {ranked[-1]}"
    else:
        problem = "No major problem detected"
    weak = ranked[-1]
    hr = pat["typical_habit_hour"].get(weak)
    change = (f"Move {weak} before {hr + 1}:00 (you usually do it around {hr}:00)." if hr is not None
              else f"Give {weak} a fixed daily time and protect it.")
    return {"start": start.isoformat(), "end": end.isoformat(), "average_score": round(sum(x["score"] for x in scores) / 7),
            "daily_scores": [{"date": x["date"], "score": x["score"]} for x in scores],
            "tasks_completed": completed or 0, "tasks_postponed": int(postponed or 0), "habits": habits,
            "strongest_habit": ranked[0], "weakest_habit": weak, "biggest_problem": problem,
            "one_change": change, "patterns": pat}
