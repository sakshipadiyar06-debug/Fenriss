from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from app.core import timeutil as tu
from sqlalchemy import select
from app.models import HabitLog, LeetCodeEntry, NewsItem, User
from app.services import news_service as ns
from app.services import scoring_service as sc
from app.services import task_service as ts
from app.services import planner_service as pl
from app.services import workflow_service as wf
from app.services.ai_service import rule_parse

TZ = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 12)  # a Monday
NOW = datetime(2026, 10, 12, 9, 0)


def eod(d):
    return ts.end_of_day(d)


# ---- tasks
def test_task_creation_defaults(db):
    t = ts.create_task(db, "  Write report ")
    assert t.title == "Write report" and t.status == "pending" and t.priority == "medium" and t.postponement_count == 0


def test_invalid_priority_rejected(db):
    try:
        ts.create_task(db, "x", priority="urgent")
        assert False
    except ValueError:
        pass


def test_task_completion_sets_timestamp_and_spawns_recurring(db):
    t = ts.create_task(db, "Standup", deadline=eod(TODAY), recurring=True, recurrence_rule="daily")
    ts.complete_task(db, t, actual_minutes=15)
    assert t.status == "completed" and t.completed_at and t.actual_minutes == 15
    assert len(ts.rank_tasks(db, NOW)) == 1  # next occurrence created


def test_postponement_counted(db):
    t = ts.create_task(db, "Report", deadline=eod(TODAY))
    ts.postpone(db, t, eod(TODAY + timedelta(days=1)))
    ts.postpone(db, t, eod(TODAY + timedelta(days=2)))
    assert t.postponement_count == 2
    assert ts.procrastination(db)[0]["postponed"] == 2


def test_prioritization_and_classification(db):
    crit = ts.create_task(db, "Pay fees", priority="critical", deadline=eod(TODAY))
    low = ts.create_task(db, "Tidy desk", priority="low")
    mid = ts.create_task(db, "Read article", priority="medium", deadline=eod(TODAY + timedelta(days=20)))
    g = ts.classify(ts.rank_tasks(db, NOW))
    assert [t.id for t, _ in g["must"]] == [crit.id]
    assert low.id in [t.id for t, _ in g["nice"]] and mid.id in [t.id for t, _ in g["nice"]]


def test_not_everything_is_must(db):
    for i in range(8):
        ts.create_task(db, f"T{i}", priority="critical", deadline=eod(TODAY))
    assert len(ts.classify(ts.rank_tasks(db, NOW))["must"]) <= 5


def test_blocked_task_demoted(db):
    a = ts.create_task(db, "Design", priority="medium")
    b = ts.create_task(db, "Build", priority="critical", deadline=eod(TODAY), depends_on_id=a.id)
    ranked = {t.id: (s, blk) for t, s, blk in ts.rank_tasks(db, NOW)}
    assert ranked[b.id][1] is True and ranked[b.id][0] < 65


def test_deadline_parsing():
    assert ts.parse_deadline_text("by Friday", TODAY).date() == date(2026, 10, 16)
    assert ts.parse_deadline_text("tomorrow", TODAY).date() == date(2026, 10, 13)
    assert ts.parse_deadline_text("in 3 days", TODAY).date() == date(2026, 10, 15)
    assert ts.parse_deadline_text("whenever", TODAY) is None


def test_rule_parse_examples():
    assert rule_parse("Add finish my project report by Friday.", TODAY)["intent"] == "add_task"
    r = rule_parse("I finished LeetCode for 40 minutes.", TODAY)
    assert r["intent"] == "log_leetcode" and r["minutes"] == 40
    r = rule_parse("I studied React for 45 minutes.", TODAY)
    assert r["intent"] == "log_course" and r["minutes"] == 45
    assert rule_parse("Good morning", TODAY)["intent"] == "morning"


# ---- habits / streaks
def log(db, habit, d, minutes):
    db.add(HabitLog(habit=habit, date=d, minutes=minutes))
    db.commit()


def test_streak_pure():
    met = {TODAY - timedelta(days=i) for i in range(1, 4)}
    assert sc.compute_streak(met, TODAY) == 3  # today not done yet doesn't break it
    assert sc.compute_streak(met | {TODAY}, TODAY) == 4
    assert sc.compute_streak({TODAY - timedelta(days=2)}, TODAY) == 0


def test_course_habit_tracking(db):
    log(db, "course", TODAY, 20)
    assert not sc.habit_stats(db, "course", TODAY)["met_today"]
    log(db, "course", TODAY, 15)  # sessions accumulate
    s = sc.habit_stats(db, "course", TODAY)
    assert s["met_today"] and s["today_minutes"] == 35 and s["streak"] == 1


def test_leetcode_tracking_and_weak_topics(db):
    u = db.scalar(select(User))
    for st in ("solved", "solved", "solved"):
        db.add(LeetCodeEntry(date=TODAY, topic="Arrays", status=st, time_spent=20))
    for st in ("stuck", "attempted", "solved"):
        db.add(LeetCodeEntry(date=TODAY, topic="Graphs", status=st, time_spent=45))
    db.commit()
    a = sc.leetcode_analysis(db)
    assert a["topics"]["Arrays"]["rating"] == "strong" and a["topics"]["Graphs"]["rating"] == "weak"
    assert a["total_solved"] == 4
    wf.log_leetcode(db, u, 30, topic="Trees", problem_name="Invert Tree")
    assert sc.minutes_on(db, "leetcode", wf.local_today(tu.get_tz(u.timezone))) == 30


def test_week_streaks_and_counts(db):
    for i in range(5):
        log(db, "course", TODAY - timedelta(days=i), 30)
    s = sc.habit_stats(db, "course", TODAY)
    assert s["streak"] == 5 and s["week_done"] == 5 and s["month_done"] == 5


# ---- scores
def test_weighted_score_math():
    assert sc.weighted_score({k: 100 for k in sc.WEIGHTS}) == 100
    assert sc.weighted_score({k: 0 for k in sc.WEIGHTS}) == 0
    comps = {"productivity": 85, "course": 100, "leetcode": 100, "consistency": 75, "planning": 80, "personal_development": 70}
    assert sc.weighted_score(comps) == 88  # 25.5+20+20+11.25+8+3.5


def test_daily_and_weekly_score(db):
    log(db, "course", TODAY, 30)
    log(db, "leetcode", TODAY, 30)
    log(db, "planning", TODAY, 0)
    d = sc.daily_score(db, TODAY, TZ)
    assert d["components"]["course"] == 100 and d["components"]["planning"] == 100 and d["score"] > 50
    w = sc.week_summary(db, TODAY, TZ)
    assert w["habits"]["course"]["days"] == 1 and 0 <= w["average_score"] <= 100 and len(w["daily_scores"]) == 7


# ---- planner
def test_unrealistic_workload_flagged(db):
    u = db.scalar(select(User))
    u.daily_hours, u.wake_time, u.sleep_time = 2, "07:00", "23:00"
    for i in range(6):
        ts.create_task(db, f"Big {i}", priority="high", deadline=eod(TODAY), estimated_minutes=120)
    db.commit()
    plan = pl.build_plan(db, u, NOW)
    assert not plan["workload"]["realistic"] and "We need to cut or move tasks" in plan["workload"]["message"]
    assert plan["to_move"]
    assert {b["title"] for b in plan["schedule"]} >= {"Course", "LeetCode"}  # mandatory blocks protected


# ---- news
def item(title, source, tier, hours_ago=1, cat="finance"):
    return NewsItem(title=title, url=f"https://x/{abs(hash(title + source))}", source=source, source_tier=tier, category=cat,
                    published_at=tu.utcnow() - timedelta(hours=hours_ago), content_hash=ns.content_hash(source, title))


def test_news_deduplication(db):
    a = {"source": "BBC", "title": "RBI holds repo rate!"}
    b = {"source": "bbc", "title": "RBI  holds repo rate"}
    assert len(ns.dedupe([a, b, {"source": "Reuters", "title": "RBI holds repo rate"}])) == 2
    assert ns.content_hash("BBC", "RBI holds repo rate!") == ns.content_hash("bbc", "rbi holds repo rate")


def test_source_ranking_and_classification():
    assert ns.classify_source("Reserve Bank of India", "https://rbi.org.in/x") == 1
    assert ns.classify_source("anything", "https://pib.gov.in/x") == 1
    assert ns.classify_source("Reuters", "https://news.google.com/rss/articles/abc") == 2  # aggregator link, trusted publisher
    assert ns.classify_source("Random Blog", "https://news.google.com/x") == 3
    assert ns.BASE_TRUST[1] > ns.BASE_TRUST[2] > ns.BASE_TRUST[3]


def test_cross_check_boosts_trust_and_orders_briefing(db):
    db.add_all([item("RBI keeps repo rate unchanged at 5.5 percent", "Reuters", 2),
                item("RBI keeps repo rate unchanged at 5.5 percent says governor", "The Hindu", 2),
                item("Celebrity feud erupts online today again", "Gossip Blog", 3),
                item("Nvidia unveils new data centre chip for AI", "BBC News", 2, cat="technology")])
    db.commit()
    ns.recompute_scores(db)
    b = ns.get_briefing(db, analyze=False)
    top = b["top"][0]
    assert "RBI" in top["title"] and top["corroboration"] == 2 and top["verification"].startswith("Confirmed by 2")
    assert top["trust_score"] > ns.BASE_TRUST[2]
    gossip = [i for i in b["top"] if i["source"] == "Gossip Blog"][0]
    assert gossip["verification"].startswith("Unverified")


def test_news_failure_message(db):
    ns.LAST["ok"] = False
    assert ns.get_briefing(db)["message"] == "News service temporarily unavailable."
    ns.LAST["ok"] = None


def test_one_bad_feed_does_not_break_collection(db):
    class Client:
        def get(self, url):
            raise RuntimeError("down")
        def close(self): pass
    r = ns.collect_news(db, [{"name": "X", "url": "u", "tier": 1, "category": "world"}], Client())
    assert r["failed_feeds"] == 1


# ---- time handling
def test_timezone_day_bounds_ist():
    s, e = tu.day_bounds_utc(date(2026, 10, 12), TZ)
    assert s == datetime(2026, 10, 11, 18, 30) and e == datetime(2026, 10, 12, 18, 30)
    assert tu.to_local(datetime(2026, 10, 11, 20, 0), TZ).date() == date(2026, 10, 12)
    assert tu.get_tz("Not/AZone").key == "Asia/Kolkata"
    assert tu.parse_hhmm("21:30").hour == 21
