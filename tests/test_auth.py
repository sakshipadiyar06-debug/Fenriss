from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.core import security as sec
from app.core.database import SessionLocal
from app.main import app
from app.models import AuthSession, HabitLog, NewsItem, Task, User
from app.scheduler import jobs
from app.services.ai_service import ai
from app.core.timeutil import utcnow


@pytest.fixture(scope="module")
def clients():
    with TestClient(app) as a, TestClient(app) as b:
        yield a, b


def reg(c, email, pw="correct horse 9", name="Test"):
    return c.post("/api/auth/register", json={"email": email, "password": pw, "name": name})


@pytest.fixture(autouse=True)
def _reset_limits():
    for l in (sec.login_by_account, sec.login_by_ip, sec.register_by_ip):
        l.reset()


def test_register_login_logout_flow(clients):
    a, _ = clients
    assert a.get("/api/dashboard").status_code == 401 and a.get("/api/tasks").status_code == 401
    r = reg(a, "Alice@Example.com")
    assert r.status_code == 201 and r.json()["user"]["email"] == "alice@example.com"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert a.get("/api/auth/me").status_code == 200 and a.get("/api/dashboard").status_code == 200
    old = a.cookies.get(sec.COOKIE)
    a.post("/api/auth/logout")
    assert a.get("/api/auth/me").status_code == 401
    with TestClient(app) as replay:  # a stolen/old cookie must be dead server-side
        replay.cookies.set(sec.COOKIE, old)
        assert replay.get("/api/auth/me").status_code == 401
    assert a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"}).status_code == 200


def test_password_rules_and_duplicates(clients):
    a, b = clients
    assert reg(b, "bob@example.com", pw="short").status_code == 422
    assert reg(b, "not-an-email").status_code == 422
    assert reg(b, "bob@example.com").status_code == 201
    assert reg(b, "BOB@example.com").status_code == 409
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == "bob@example.com"))
        assert u.password_hash.startswith("pbkdf2_sha256$") and "correct horse" not in u.password_hash
        assert not db.scalars(select(AuthSession).where(AuthSession.token_hash == b.cookies.get(sec.COOKIE))).all()  # only hashes stored


def test_login_failures_and_rate_limit(clients):
    a, _ = clients
    for _ in range(8):
        assert a.post("/api/auth/login", json={"email": "alice@example.com", "password": "wrong-password"}).status_code == 401
    assert a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"}).status_code == 429
    assert a.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 401


def test_setup_never_exposes_password_hash(clients):
    a, b = clients
    a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"})
    body = a.get("/api/setup").json()
    assert "password_hash" not in body and body["email"] == "alice@example.com"


def test_data_isolation_between_users(clients):
    a, b = clients
    a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"})
    tid = a.post("/api/tasks", json={"title": "Alice secret task", "priority": "critical"}).json()["id"]
    a.post("/api/habits", json={"habit": "course", "minutes": 45, "topic": "Alice topic"})
    a.post("/api/habits/leetcode", json={"problem_name": "Alice problem", "time_spent": 30, "topic": "Graphs"})
    gid = a.post("/api/goals", json={"title": "Alice goal", "milestones": ["m1"]}).json()["id"]
    a.post("/api/assistant/chat", json={"message": "remember that Alice likes mornings"})
    a.post("/api/setup", json={"name": "Alice", "interests": "alice-only-interest", "goals": "g"})
    assert a.get("/api/tasks").json() and a.get("/api/memory").json() and a.get("/api/habits").json()["habits"]["course"]["today_minutes"] == 45
    # --- Bob sees none of it, on every read path
    assert b.get("/api/tasks").json() == []
    assert b.get("/api/memory").json() == [] and b.get("/api/goals").json() == []
    assert b.get("/api/habits").json()["habits"]["course"]["today_minutes"] == 0
    assert b.get("/api/habits/leetcode").json()["recent"] == [] and b.get("/api/habits/leetcode").json()["analysis"]["total_solved"] == 0
    d = b.get("/api/dashboard").json()
    assert d["tasks"] == [] and d["goals"] == [] and d["habits"]["course"]["streak"] == 0
    assert "Alice" not in str(d) and "Alice" not in str(b.get("/api/analytics").json())
    assert "alice-only-interest" not in str(b.get("/api/setup").json())
    # --- Bob cannot touch Alice's objects by id
    assert b.patch(f"/api/tasks/{tid}", json={"status": "completed"}).status_code == 404
    assert b.delete(f"/api/tasks/{tid}").status_code == 404
    assert b.post(f"/api/goals/{gid}/milestones/0").status_code == 404
    assert b.post("/api/assistant/chat", json={"message": "I finished Alice secret task"}).json()["reply"].startswith("I couldn't find")
    assert a.get("/api/tasks").json()[0]["status"] == "pending"  # untouched
    with SessionLocal() as db:
        db.info["system"] = True
        assert db.scalar(select(Task.status).where(Task.id == tid)) == "pending"


def test_news_is_shared_but_private_data_is_not(clients):
    a, b = clients
    with SessionLocal() as db:
        db.add(NewsItem(title="Shared headline about RBI policy", url="https://x.example/1", source="Reuters", source_tier=2,
                        category="finance", published_at=utcnow(), content_hash="h-shared-1"))
        db.commit()
    for c in (a, b):
        assert any("Shared headline" in i["title"] for i in c.get("/api/news").json()["top"])


def test_deny_by_default_without_user(clients):
    with SessionLocal() as db:  # no uid bound: must see nothing and write nothing
        assert db.scalars(select(HabitLog)).all() == [] and db.scalars(select(Task)).all() == []
        db.add(Task(title="orphan"))
        with pytest.raises(Exception):
            db.commit()


def test_cross_origin_write_blocked(clients):
    a, _ = clients
    a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"})
    assert a.post("/api/tasks", json={"title": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert a.post("/api/tasks", json={"title": "x"}, headers={"Origin": "http://testserver"}).status_code == 201


def test_security_headers_and_docs_off(clients):
    a, _ = clients
    r = a.get("/")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert a.get("/docs").status_code == 404 and a.get("/api/tasks").headers["cache-control"] == "no-store"


def test_change_password_signs_out_other_sessions(clients):
    _, b = clients
    with TestClient(app) as other:
        other.post("/api/auth/login", json={"email": "bob@example.com", "password": "correct horse 9"})
        assert other.get("/api/auth/me").status_code == 200
        assert b.post("/api/auth/change-password", json={"current_password": "bad", "new_password": "new password 12"}).status_code == 403
        assert b.post("/api/auth/change-password", json={"current_password": "correct horse 9", "new_password": "new password 12"}).status_code == 200
        assert other.get("/api/auth/me").status_code == 401 and b.get("/api/auth/me").status_code == 200


def test_delete_account_erases_everything(clients):
    a, b = clients
    a.post("/api/auth/login", json={"email": "alice@example.com", "password": "correct horse 9"})
    assert a.post("/api/auth/delete-account", json={"password": "wrong"}).status_code == 403
    assert a.post("/api/auth/delete-account", json={"password": "correct horse 9"}).status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        db.info["system"] = True
        assert db.scalar(select(User).where(User.email == "alice@example.com")) is None
        assert db.scalars(select(Task).where(Task.title.like("Alice%"))).all() == []
        assert db.scalars(select(HabitLog).where(HabitLog.topic == "Alice topic")).all() == []
    assert b.get("/api/tasks").status_code == 200  # Bob unaffected


def test_ai_daily_limit_per_user():
    ai._usage.clear()
    assert all(ai.allow(1) for _ in range(40)) and not ai.allow(1) and ai.allow(2)


def test_scheduler_uses_each_users_timezone_and_times():
    u = User(email="s@e.com", password_hash="x", timezone="Asia/Kolkata", morning_time="08:00", course_time="18:00",
             leetcode_time="19:00", evening_time="21:30")
    d = datetime(2026, 10, 12)  # Monday
    assert jobs.due_jobs(u, d.replace(hour=8, minute=3)) == ["morning"]
    assert jobs.due_jobs(u, d.replace(hour=8, minute=30)) == []
    assert jobs.due_jobs(u, d.replace(hour=19, minute=0)) == ["leetcode"]
    sunday = datetime(2026, 10, 11, 21, 46)
    assert "weekly" in jobs.due_jobs(u, sunday)


def test_aggregate_queries_are_scoped_too(db):
    """count()/sum()/min() style queries (used for streaks, stats) must not see other users' rows."""
    from datetime import date
    from sqlalchemy import func
    other = User(email="o@example.com", password_hash="x", name="O")
    db.add(other)
    db.commit()
    db.info["uid"] = other.id
    db.add_all([HabitLog(habit="course", date=date(2026, 10, 1), minutes=99), Task(title="other task")])
    db.commit()
    mine = db.scalar(select(User).where(User.email == "t@example.com"))
    db.info["uid"] = mine.id
    assert db.scalar(select(func.count()).select_from(HabitLog)) == 0
    assert db.scalar(select(func.sum(HabitLog.minutes))) is None
    assert db.scalar(select(func.min(HabitLog.date))) is None
    assert db.scalar(select(func.count()).select_from(Task)) == 0
    assert db.execute(select(HabitLog.date, func.sum(HabitLog.minutes)).group_by(HabitLog.date)).all() == []
    db.info["uid"] = other.id
    assert db.scalar(select(func.sum(HabitLog.minutes))) == 99
