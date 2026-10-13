"""Application workflows: dashboard, morning, evening, weekly, and the natural-language dispatcher."""
import json
import logging
from datetime import date, datetime, timedelta
from sqlalchemy import func, select
from app.core.timeutil import get_tz, local_now, local_today, parse_hhmm
from app.models import (ChatMessage, Course, DailyReview, Goal, HabitLog, LeetCodeEntry, Memory,
                        Task, User, WeeklyReview)
from app.prompts.assistant_prompt import SYSTEM_PROMPT, build_context
from app.services import news_service as ns
from app.services import planner_service as pl
from app.services import scoring_service as sc
from app.services import task_service as ts
from app.services.ai_service import ai, rule_parse

log = logging.getLogger("fenris.workflow")
BAR = lambda v, t, n=10: "█" * min(n, round(n * v / t)) + "░" * (n - min(n, round(n * v / t)))
ICON = {"must": "🔴", "should": "🟡", "nice": "🟢"}


def to_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
        return d if "T" in str(s) or " " in str(s) else ts.end_of_day(d.date())
    except ValueError:
        return None


def log_habit(db, user: User, habit: str, minutes: int, topic="", notes="", day: date | None = None) -> HabitLog:
    day = day or local_today(get_tz(user.timezone))
    row = HabitLog(habit=habit, date=day, minutes=minutes, topic=topic or "", notes=notes or "", completed=True)
    db.add(row)
    if habit == "course" and topic:
        c = db.scalar(select(Course).where(Course.active == True))  # noqa: E712
        if c:
            c.current_topic = topic
    db.commit()
    return row


def log_leetcode(db, user, minutes: int, **kw) -> LeetCodeEntry:
    day = local_today(get_tz(user.timezone))
    e = LeetCodeEntry(date=day, problem_name=kw.get("problem_name") or "", problem_url=kw.get("problem_url") or "",
                      difficulty=kw.get("difficulty") or "Medium", topic=kw.get("topic") or "Other",
                      status=kw.get("status") or "solved", time_spent=minutes, notes=kw.get("notes") or "")
    db.add(e)
    db.commit()
    log_habit(db, user, "leetcode", minutes, e.topic, e.notes, day)
    return e


def goals_progress(db) -> list[dict]:
    out = []
    for g in db.scalars(select(Goal).where(Goal.status == "active")):
        if g.metric == "leetcode_solved" and g.target_value:
            cur = sc.leetcode_analysis(db)["total_solved"]
            pct, basis = min(100, 100 * cur / g.target_value), f"{cur}/{int(g.target_value)} problems solved"
        elif g.metric == "course_progress":
            c = db.scalar(select(Course).where(Course.active == True))  # noqa: E712
            pct, basis = (c.progress_pct if c else 0), "current course progress"
        else:
            ms = g.milestones or []
            done = sum(1 for m in ms if m.get("done"))
            pct, basis = (100 * done / len(ms) if ms else 0), f"{done}/{len(ms)} milestones"
        out.append({"id": g.id, "title": g.title, "target_date": g.target_date.isoformat() if g.target_date else None,
                    "percent": round(pct), "basis": basis, "milestones": g.milestones or []})
    return out


def upcoming(db, today: date) -> dict:
    out = {}
    for t in db.scalars(select(Task).where(Task.status.in_(ts.OPEN), Task.deadline.is_not(None)).order_by(Task.deadline)):
        n = (t.deadline.date() - today).days
        if n < 1 or n > 14:
            continue
        label = "Tomorrow" if n == 1 else (t.deadline.strftime("%A") if n < 7 else "Next week")
        out.setdefault(label, []).append({"id": t.id, "title": t.title, "priority": t.priority, "deadline": t.deadline.isoformat()})
    return out


def dashboard(db, user: User) -> dict:
    tz = get_tz(user.timezone)
    now, today = local_now(tz), local_today(tz)
    groups = ts.classify(ts.rank_tasks(db, now))
    today_tasks = [dict(ts.task_dict(t), tier=k, score=round(s)) for k in ("must", "should", "nice")
                   for t, s in groups[k] if k != "nice" or (t.deadline and t.deadline.date() <= today + timedelta(days=2)) or len(groups[k]) <= 5]
    stats = {h: sc.habit_stats(db, h, today) for h in sc.HABITS}
    score = sc.daily_score(db, today, tz)
    plan = pl.build_plan(db, user, now)
    try:
        briefing = ns.get_briefing(db, user.news_interests, analyze=False)
    except Exception:
        log.exception("news in dashboard failed")
        briefing = {"available": False, "message": "News service temporarily unavailable.", "top": [], "categories": {}}
    course = db.scalar(select(Course).where(Course.active == True))  # noqa: E712
    return {"date": today.isoformat(), "weekday": today.strftime("%A"), "user": {"name": user.name, "setup_complete": user.setup_complete},
            "score": score, "yesterday_score": sc.daily_score(db, today - timedelta(days=1), tz)["score"],
            "habits": stats, "tasks": today_tasks, "focus": plan["focus"], "workload": plan["workload"],
            "unfinished_yesterday": plan["unfinished_yesterday"], "upcoming": upcoming(db, today),
            "news": briefing, "goals": goals_progress(db), "procrastination": ts.procrastination(db, 3),
            "course": {"name": course.name, "topic": course.current_topic, "progress": course.progress_pct} if course else None,
            "course_trend": sc.course_trend(db, today),
            "notifications": [], "ai_enabled": ai.configured}


# ---------------------------------------------------------------- text renderers
def _tasks_lines(plan: dict, n=3) -> list[str]:
    rows = [(k, t) for k in ("must", "should") for t in plan["groups"][k]][:n]
    return [f"{i}. {ICON[k]} {t['title']}" + (f" (due {t['deadline'][:10]})" if t["deadline"] else "") for i, (k, t) in enumerate(rows, 1)]


def news_text(b: dict, user_interests: str = "", db=None) -> str:
    if not b["available"]:
        return b["message"]
    L = ["📰 DAILY BRIEFING", "", "🔥 TOP 5", ""]
    for n, i in enumerate(b["top"], 1):
        L.append(f"{n}. {i['title']}  [{i['source']} · {i['verification']}]")
        br = i.get("briefing")
        if br:
            L += [f"   What happened (confirmed): {br.get('what_happened', '')}", f"   Why it matters (analysis): {br.get('why_it_matters', '')}",
                  f"   What next (speculation): {br.get('what_next', '')}"]
        elif i["summary"]:
            L.append(f"   {i['summary'][:200]}")
    for cat, icon in (("technology", "💻 TECHNOLOGY"), ("finance", "💰 FINANCE"), ("india", "🇮🇳 INDIA"), ("world", "🌎 WORLD")):
        L += ["", "────────────────────", "", icon, ""]
        L += [f"• {i['title']}  [{i['source']} · {i['verification']}]" for i in b["categories"].get(cat, [])] or ["• Nothing significant collected."]
    if db is not None and (w := ns.what_matters(db, user_interests, b)):
        L += ["", "────────────────────", "", "🎯 WHAT MATTERS TO YOU", "", w]
    return "\n".join(L)


def morning(db, user: User) -> dict:
    tz = get_tz(user.timezone)
    today = local_today(tz)
    plan = pl.build_plan(db, user)
    pl.record_planning(db, user)
    y = sc.daily_score(db, today - timedelta(days=1), tz)
    stats = {h: sc.habit_stats(db, h, today) for h in sc.MANDATORY}
    L = [f"Good morning{', ' + user.name if user.name != 'there' else ''}.", "", "TODAY'S SCORE",
         f"Yesterday: {y['score']}/100" if db.scalar(select(func.count()).select_from(HabitLog).where(HabitLog.date == today - timedelta(days=1))) else "Yesterday: no activity recorded"]
    if plan.get("recovery"):
        L += ["", plan["workload"]["recovery"]]
    L += ["", "TOP PRIORITIES", ""] + (_tasks_lines(plan) or ["No tasks yet. Add one thing that matters today."])
    L += ["", "MANDATORY", "", f"📚 Course — 30 min (streak {stats['course']['streak']})", f"💻 LeetCode — 30 min (streak {stats['leetcode']['streak']})"]
    if plan["workload"]["message"]:
        L += ["", "⚠️ " + plan["workload"]["message"]]
        if plan["to_move"]:
            L.append("Move or cut: " + ", ".join(t["title"] for t in plan["to_move"][:5]))
    if plan["unfinished_yesterday"]:
        L += ["", "UNFINISHED (not auto-moved)"] + [f"• {u['title']} — {u['reason']}" for u in plan["unfinished_yesterday"][:4]]
    if plan["schedule"]:
        L += ["", "SCHEDULE", ""] + [f"{s['start']} — {s['title']}" for s in plan["schedule"]]
    b = ns.get_briefing(db, user.news_interests)
    L += ["", "NEWS", "", news_text(b, user.news_interests, db)] if b["available"] else ["", "NEWS", "", b["message"]]
    trend = sc.course_trend(db, today)
    if trend["message"]:
        L += ["", "COURSE: " + trend["message"]]
    L += ["", "TODAY'S FOCUS", "", plan["focus"][0] if plan["focus"] else "Protect your 60 mandatory minutes.", "", "Let's get started."]
    return {"text": "\n".join(L), "plan": plan}


def evening(db, user: User, answers: dict | None = None) -> dict:
    tz = get_tz(user.timezone)
    today = local_today(tz)
    sc_ = sc.daily_score(db, today, tz)
    s, e = __import__("app.core.timeutil", fromlist=["x"]).day_bounds_utc(today, tz)
    done = db.scalars(select(Task).where(Task.completed_at >= s, Task.completed_at < e)).all()
    due = db.scalars(select(Task).where(Task.deadline >= datetime.combine(today, datetime.min.time()),
                                        Task.deadline < datetime.combine(today + timedelta(days=1), datetime.min.time()),
                                        Task.status != "cancelled")).all()
    open_due = [t for t in due if t.status != "completed"]
    total = len({t.id for t in done} | {t.id for t in due})
    cm, lm = sc.minutes_on(db, "course", today), sc.minutes_on(db, "leetcode", today)
    ok = lambda m: f"✅ {m} minutes" if m >= 30 else f"⬜ {m}/30 minutes"
    pat = sc.patterns(db, tz, today, 28, user.daily_hours)
    well = [x for x in [f"{len(done)} task(s) completed" if done else None, "Course target met" if cm >= 30 else None,
                        "LeetCode target met" if lm >= 30 else None] if x]
    bad = [x for x in [f"{len(open_due)} task(s) due today still open" if open_due else None,
                       "Course not done" if cm < 30 else None, "LeetCode not done" if lm < 30 else None] if x]
    if pat["avoided_tasks"]:
        pattern = f"'{pat['avoided_tasks'][0]['title']}' has been postponed {pat['avoided_tasks'][0]['postponed']} times."
    elif pat["best_task_hours"]:
        pattern = f"You complete most tasks around {pat['best_task_hours'][0]}:00."
    else:
        pattern = "Not enough history yet to show a reliable pattern."
    tomorrow = [t for t in db.scalars(select(Task).where(Task.status.in_(ts.OPEN), Task.deadline.is_not(None))) if t.deadline.date() == today + timedelta(days=1)]
    L = ["DAILY REVIEW", "", "Tasks:", f"{len([t for t in done])}/{total} completed", "", "Course:", ok(cm), "", "LeetCode:", ok(lm),
         "", "Score:", f"{sc_['score']}/100", "  " + " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in sc_["components"].items()),
         "", "WHAT WENT WELL", *(["• " + x for x in well] or ["• Nothing recorded yet."]),
         "", "WHAT DIDN'T", *(["• " + x for x in bad] or ["• Nothing flagged."]), "", "PATTERN", pattern]
    if sc_["score"] < 60 or cm < 30 or lm < 30:
        L += ["", "Today was below target. That's fine. Let's make tomorrow manageable and restart the streak."]
    note = ai.coach_note({"score": sc_, "well": well, "bad": bad, "pattern": pat}) if ai.configured and ai.allow(user.id) else None
    if note:
        L += ["", note]
    L += ["", "TOMORROW", *(["• " + t.title for t in tomorrow] or ["• Nothing scheduled yet."]), "", "What is happening tomorrow?"]
    questions = ["What got in the way of the unfinished items?"] if bad else []
    questions += ["What went well?", "What went badly?", "What needs to change tomorrow?"]
    text = "\n".join(L)
    rev = db.scalar(select(DailyReview).where(DailyReview.date == today)) or DailyReview(date=today)
    rev.score, rev.breakdown, rev.text = sc_["score"], sc_["components"], text
    if answers:
        rev.answers = answers
    db.add(rev)
    if not sc.met_set(db, "review", today, today):
        db.add(HabitLog(habit="review", date=today, minutes=0))
    db.commit()
    return {"text": text, "score": sc_, "questions": questions}


def weekly(db, user: User, force: bool = False) -> dict:
    tz = get_tz(user.timezone)
    end = local_today(tz)
    last = db.scalar(select(WeeklyReview).order_by(WeeklyReview.week_end.desc()))
    if last and not force and (end - last.week_end).days < 7:
        return {"text": last.text, "data": last.data}
    w = sc.week_summary(db, end, tz, user.daily_hours)
    h = w["habits"]
    L = ["WEEKLY REVIEW", "", f"Average score: {w['average_score']}", "", "Course:", f"{h['course']['days']}/7 days · {h['course']['minutes']} min · streak {h['course']['streak']}",
         "", "LeetCode:", f"{h['leetcode']['days']}/7 days · {h['leetcode']['minutes']} min · streak {h['leetcode']['streak']}",
         "", "Tasks:", f"{w['tasks_completed']} completed", f"{w['tasks_postponed']} postponed", "",
         f"Strongest habit: {w['strongest_habit']}", f"Weakest habit: {w['weakest_habit']}", f"Biggest problem: {w['biggest_problem']}"]
    p = w["patterns"]
    if p["avoided_tasks"]:
        L += ["", "Frequently postponed: " + ", ".join(f"{a['title']} ({a['postponed']}x)" for a in p["avoided_tasks"][:3])]
    if p["missed_deadlines"]:
        L += ["Missed deadlines: " + ", ".join(p["missed_deadlines"][:3])]
    L += ["", "ONE CHANGE FOR NEXT WEEK:", w["one_change"]]
    text = "\n".join(L)
    start = end - timedelta(days=6)
    row = db.scalar(select(WeeklyReview).where(WeeklyReview.week_start == start)) or WeeklyReview(week_start=start, week_end=end)
    row.data, row.text = w, text
    db.add(row)
    db.commit()
    return {"text": text, "data": w}


def analytics(db, user: User) -> dict:
    tz = get_tz(user.timezone)
    today = local_today(tz)
    month = [sc.daily_score(db, today - timedelta(days=i), tz)["score"] for i in range(30)]
    return {"habits": {h: sc.habit_stats(db, h, today) for h in sc.HABITS}, "leetcode": sc.leetcode_analysis(db),
            "course_trend": sc.course_trend(db, today), "patterns": sc.patterns(db, tz, today, 28, user.daily_hours),
            "score_trend_30d": list(reversed(month)), "month_average": round(sum(month) / 30, 1),
            "goals": goals_progress(db), "procrastination": ts.procrastination(db)}


# ---------------------------------------------------------------- context + chat
def build_ctx(db, user: User) -> str:
    tz = get_tz(user.timezone)
    now, today = local_now(tz), local_today(tz)
    plan = pl.build_plan(db, user, now)
    tasks = [f"[{k}] {t['title']} (priority {t['priority']}, due {t['deadline'] or 'none'}, ~{t['estimated_minutes'] or '?'} min, postponed {t['postponement_count']}x)"
             for k in ("must", "should", "nice") for t in plan["groups"][k][:8]]
    habits = [f"{h}: {st['today_minutes']}/30 min today, streak {st['streak']}, week {st['week_done']}/7, month {st['month_done']}/30"
              for h in sc.MANDATORY for st in [sc.habit_stats(db, h, today)]]
    perf = [f"{r.date}: {r.score:.0f}/100" for r in db.scalars(select(DailyReview).order_by(DailyReview.date.desc()).limit(7))]
    lc = sc.leetcode_analysis(db)
    mems = [m.content for m in db.scalars(select(Memory).order_by(Memory.id.desc()).limit(15))]
    b = ns.get_briefing(db, user.news_interests, analyze=False)
    news = [f"{i['title']} [{i['source']}, {i['verification']}]" for i in b["top"]] if b["available"] else []
    return build_context({
        "USER PROFILE": f"Name {user.name}; goals: {user.goals_text or 'n/a'}; course: {user.current_course or 'n/a'}; interests: {user.interests or 'n/a'}",
        "TODAY": f"{today:%A %Y-%m-%d}, timezone {user.timezone}, {user.daily_hours}h available",
        "TASKS": "\n".join(tasks) or "No open tasks.", "HABITS": "\n".join(habits),
        "RECENT PERFORMANCE": ("\n".join(perf) or "No reviews yet.") + f"\nLeetCode solved {lc['total_solved']}, weak topics {lc['weak'] or 'none identified'}",
        "GOALS": "\n".join(f"{g['title']}: {g['percent']}% ({g['basis']})" for g in goals_progress(db)),
        "MEMORY": "\n".join(mems), "NEWS": "\n".join(news)})


def _stats_text(db, user) -> str:
    a = analytics(db, user)
    h = a["habits"]
    L = [f"Last 30 days: average score {a['month_average']}/100", ""]
    for k, n in (("course", "Course"), ("leetcode", "LeetCode"), ("planning", "Planning"), ("review", "Review")):
        L.append(f"{n}: streak {h[k]['streak']} · week {h[k]['week_done']}/7 · month {h[k]['month_done']}/30")
    lc = a["leetcode"]
    L += ["", f"LeetCode solved: {lc['total_solved']}"] + [f"  {t}: {v['rating']} ({v['solved']}/{v['attempts']})" for t, v in lc["topics"].items()]
    if a["course_trend"]["message"]:
        L += ["", a["course_trend"]["message"]]
    return "\n".join(L)


def handle_message(db, user: User, message: str) -> dict:
    tz = get_tz(user.timezone)
    today = local_today(tz)
    use_ai = ai.configured and ai.allow(user.id)  # per-user daily cap on AI usage
    intent = (ai.parse_message(message, today, user.timezone) if use_ai else None) or rule_parse(message, today)
    kind = intent.get("intent", "chat")
    data: dict = {}
    reply = None
    try:
        if kind == "add_task" and (intent.get("title") or "").strip():
            t = ts.create_task(db, intent["title"], priority=intent.get("priority") if intent.get("priority") in ("critical", "high", "medium", "low") else "medium",
                               deadline=to_dt(intent.get("deadline")), estimated_minutes=intent.get("estimated_minutes"),
                               category=intent.get("category") or "general", description=intent.get("description") or "")
            data = ts.task_dict(t)
            reply = f"Added: {t.title} — {t.priority}" + (f", due {t.deadline:%a %d %b}" if t.deadline else ", no deadline") + (f", ~{t.estimated_minutes} min" if t.estimated_minutes else "") + f", {t.category}."
        elif kind in ("log_course", "log_leetcode"):
            mins = intent.get("minutes")
            if not mins:
                reply = "How many minutes was it?"
            elif kind == "log_course":
                log_habit(db, user, "course", int(mins), intent.get("topic") or "", intent.get("notes") or "")
                st = sc.habit_stats(db, "course", today)
                reply = f"Course: {st['today_minutes']} min today ({'target met' if st['met_today'] else str(30 - st['today_minutes']) + ' min to go'}). Streak: {st['streak']} days."
            else:
                log_leetcode(db, user, int(mins), problem_name=intent.get("problem_name"), difficulty=intent.get("difficulty"),
                             topic=intent.get("topic"), status=intent.get("status"), notes=intent.get("notes"))
                st = sc.habit_stats(db, "leetcode", today)
                reply = f"LeetCode: {st['today_minutes']} min today ({'target met' if st['met_today'] else str(30 - st['today_minutes']) + ' min to go'}). Streak: {st['streak']} days."
        elif kind in ("complete_task", "postpone_task", "couldnt_finish"):
            t = ts.find_task(db, intent.get("task_ref") or "")
            if not t:
                reply = "I couldn't find a matching open task. Which one do you mean?"
            elif kind == "complete_task":
                ts.complete_task(db, t, intent.get("minutes"))
                reply = f"Completed: {t.title}."
            elif kind == "couldnt_finish":
                reply = f"Noted. '{t.title}' is still open (postponed {t.postponement_count}x). Move it to tomorrow, or drop it?"
            else:
                nd = to_dt(intent.get("new_deadline")) or ts.end_of_day(today + timedelta(days=1))
                ts.postpone(db, t, nd)
                reply = f"Moved '{t.title}' to {nd:%a %d %b}."
                if t.postponement_count >= 3:
                    reply += f" You've postponed this {t.postponement_count} times. We should either schedule a specific 30-minute block or reconsider whether this task is actually important."
        elif kind == "show_news":
            reply = news_text(ns.get_briefing(db, user.news_interests), user.news_interests, db)
        elif kind == "whats_important":
            plan = pl.build_plan(db, user)
            reply = "\n".join(["TOP PRIORITIES", *(_tasks_lines(plan) or ["No open tasks."]), "", "MANDATORY: Course 30 min, LeetCode 30 min"] + ([plan["workload"]["message"]] if plan["workload"]["message"] else []))
        elif kind == "plan_day":
            plan = pl.build_plan(db, user)
            pl.record_planning(db, user)
            data = plan
            reply = "\n".join(["TODAY'S PLAN", *_tasks_lines(plan), "", *[f"{s['start']} — {s['title']}" for s in plan["schedule"]]] + (["", "⚠️ " + plan["workload"]["message"]] if plan["workload"]["message"] else []))
        elif kind == "morning":
            reply = morning(db, user)["text"]
        elif kind == "end_day":
            r = evening(db, user)
            reply = r["text"]
            data = {"questions": r["questions"]}
        elif kind == "procrastination":
            p = ts.procrastination(db)
            reply = "\n".join(f"• {x['title']}: {x['message']}" for x in p) or "Nothing is being postponed repeatedly."
        elif kind == "stats":
            reply = _stats_text(db, user)
        elif kind == "remember" and intent.get("content"):
            db.add(Memory(content=intent["content"][:500]))
            db.commit()
            reply = "Remembered."
    except ValueError as e:
        reply = f"That didn't work: {e}"
    ai_used = False
    if reply is None:
        hist = [{"role": m.role, "content": m.content} for m in reversed(db.scalars(select(ChatMessage).order_by(ChatMessage.id.desc()).limit(8)).all())]
        reply = ai.chat(SYSTEM_PROMPT, build_ctx(db, user), hist, message) if use_ai else None
        ai_used = reply is not None
        reply = reply or ("I couldn't map that to an action" + ("" if use_ai else (" (daily AI limit reached)" if ai.configured else " (AI is off on this server)")) +
                          ". Try: 'Add <task> by Friday', 'I studied React for 45 minutes', 'I finished LeetCode for 40 minutes', "
                          "'Plan my day', 'Show me today's news', 'End my day'.")
    db.add(ChatMessage(role="user", content=message))
    db.add(ChatMessage(role="assistant", content=reply))
    db.commit()
    return {"reply": reply, "intent": kind, "data": data, "ai_used": ai_used}
