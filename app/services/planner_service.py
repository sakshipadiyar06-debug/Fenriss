"""Daily planning: realistic workload check, protected mandatory blocks, buffers."""
from datetime import date, datetime, time, timedelta
from sqlalchemy import func, select
from app.core.timeutil import get_tz, local_now, local_today, parse_hhmm
from app.models import HabitLog, User
from app.services import scoring_service as sc
from app.services import task_service as ts

BUFFER = 10  # minutes between blocks


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def recovery_needed(db, today: date) -> bool:
    """Mandatory habits missed on 2+ of the last 3 days -> make today lighter."""
    first = db.scalar(select(func.min(HabitLog.date)))
    if not first or first > today - timedelta(days=3):
        return False  # not enough history to call it a slump
    miss = 0
    for i in (1, 2, 3):
        d = today - timedelta(days=i)
        if not all(d in sc.met_set(db, h, d, d) for h in sc.MANDATORY):
            miss += 1
    return miss >= 2


def build_plan(db, user: User, now: datetime | None = None) -> dict:
    tz = get_tz(user.timezone)
    now = now or local_now(tz)
    today = now.date()
    ranked = ts.rank_tasks(db, now)
    groups = ts.classify(ranked)
    factor = sc.patterns(db, tz, today, 28)["estimation_factor"]
    factor = min(2.0, max(0.8, factor))
    est = lambda t: int((t.estimated_minutes or 30) * factor)

    recovery = recovery_needed(db, today)
    wake = datetime.combine(today, parse_hhmm(user.wake_time, "07:00"))
    sleep = datetime.combine(today, parse_hhmm(user.sleep_time, "23:00"))
    if sleep <= wake:
        sleep += timedelta(days=1)
    start = max(now, wake + timedelta(minutes=30))
    start = start.replace(second=0, microsecond=0) + timedelta(minutes=(-start.minute) % 5)
    mand_min = sc.target_for("course") + sc.target_for("leetcode")
    available = min(user.daily_hours * 60, max(0, (sleep - start).total_seconds() / 60))
    if recovery:
        available *= 0.6
    capacity = max(0, available * 0.85 - mand_min)  # 15% slack for buffers/interruptions

    candidates = [t for t, _ in groups["must"]] + [t for t, _ in groups["should"]]
    total = sum(est(t) for t in candidates)
    planned, to_move, used = [], [], 0
    for t, _ in groups["must"]:  # must-dos are always planned
        planned.append(t)
        used += est(t)
    should_ids = {t.id for t, _ in groups["should"]}
    for t, _ in groups["should"] + groups["nice"][:3]:
        if used + est(t) <= capacity:
            planned.append(t)
            used += est(t)
        elif t.id in should_ids:
            to_move.append(t)

    workload = {"estimated_minutes": total + mand_min, "available_minutes": int(available),
                "capacity_for_tasks": int(capacity), "realistic": total <= capacity, "message": None}
    if total > capacity:
        workload["message"] = (f"You have approximately {round((total + mand_min) / 60, 1)} hours of work but only "
                               f"{round(available / 60, 1)} hours available. We need to cut or move tasks.")
    if recovery:
        workload["recovery"] = ("Yesterday was below target. That's fine. Let's make today manageable "
                                "and restart the streak.")

    blocks = [(datetime.combine(today, parse_hhmm(user.course_time, "18:00")), sc.target_for("course"), "Course", "mandatory"),
              (datetime.combine(today, parse_hhmm(user.leetcode_time, "19:00")), sc.target_for("leetcode"), "LeetCode", "mandatory")]
    blocks = [(s, s + timedelta(minutes=m), n, k) for s, m, n, k in blocks]
    schedule = [{"start": _hm(s), "end": _hm(e), "title": n, "kind": k, "task_id": None} for s, e, n, k in blocks]
    ptr, unscheduled = start, []
    for t in planned:
        dur = est(t)
        for s, e, *_ in sorted(blocks):
            if ptr < e and ptr + timedelta(minutes=dur) > s:
                ptr = e + timedelta(minutes=BUFFER)
        if ptr + timedelta(minutes=dur) > sleep:
            unscheduled.append(t)
            continue
        tier = "must" if t in [x for x, _ in groups["must"]] else "should"
        schedule.append({"start": _hm(ptr), "end": _hm(ptr + timedelta(minutes=dur)), "title": t.title,
                         "kind": tier, "task_id": t.id})
        ptr += timedelta(minutes=dur + BUFFER)
    schedule.sort(key=lambda x: x["start"])
    focus = [t.title for t, _ in (groups["must"] + groups["should"])[:3]]
    ser = lambda g: [dict(ts.task_dict(t), score=round(s)) for t, s in g]
    return {"date": today.isoformat(), "focus": focus, "mandatory": [{"habit": "course", "minutes": 30}, {"habit": "leetcode", "minutes": 30}],
            "groups": {k: ser(v) for k, v in groups.items()}, "schedule": schedule, "workload": workload,
            "to_move": [ts.task_dict(t) for t in to_move + unscheduled],
            "unfinished_yesterday": ts.unfinished_review(db, today, now), "recovery": recovery}


def record_planning(db, user: User):
    tz = get_tz(user.timezone)
    today = local_today(tz)
    if not sc.met_set(db, "planning", today, today):
        db.add(HabitLog(habit="planning", date=today, minutes=0, completed=True))
        db.commit()
