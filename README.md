# Fenris — Personal AI Executive Assistant

A local, persistent "personal operating system": tasks, prioritisation, two protected daily habits
(Course 30 min, LeetCode 30 min), streaks, daily score, weekly review, goals, trusted-source news,
and an OpenAI-powered assistant. The AI is one component; state lives in SQLite.

## Accounts and security (multi-user)
Fenris supports many users. Each person registers with email + password and sees only their own data.
- **Passwords:** PBKDF2-SHA256 (600k iterations), never stored or logged in plain text. Minimum 8 characters.
- **Sessions:** random token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` automatically over HTTPS). Only a hash of the token is stored, so logout and password change really invalidate sessions.
- **Isolation:** every per-user table has `user_id`; a session hook adds `WHERE user_id = <you>` to every query and stamps every write, and an unauthenticated session sees nothing. Tests in `tests/test_auth.py` check this across every endpoint, including count/sum queries.
- **Abuse limits:** login attempts are rate-limited per account and per IP, sign-ups per IP, and each user gets `AI_DAILY_LIMIT` AI-assisted messages per day so nobody can run up your OpenAI bill. News refresh is throttled and shared.
- **Privacy:** users can permanently delete their account and all data (Settings, Delete my account). News is shared; personal text is never put in shared caches.
- **Other:** CSRF origin check, security headers (CSP, no framing), API docs disabled by default (`ENABLE_DOCS=true` to enable), `no-store` on API responses.
- **Not included yet:** email verification and "forgot password" (needs an email service), 2-factor login. Rate limits are in-memory (per process, reset on restart). Run **one** server process.
- **Upgrading from the single-user version:** the database schema changed. Delete `data/assistant.db` once before first run (old single-user data cannot be migrated automatically).
- To close sign-ups after creating your own account, set `ALLOW_REGISTRATION=false`.
- Once others sign up you are responsible for their data: use HTTPS, back up the database file, and tell users what you store.

## 1. Requirements
Python 3.11+. An OpenAI API key is optional: without it, structured commands, planning, scoring,
reviews and news collection all still work. Only free-form chat, news analysis and coach notes need it.

## 2-3. Install and virtual environment
```
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Environment variables
```
cp .env.example .env            # Windows: copy .env.example .env
```
Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL`. Never commit `.env`.
See `.env.example` for account/safety settings (`ALLOW_REGISTRATION`, `COOKIE_SECURE`, `AI_DAILY_LIMIT`).

## 5. Database
Created automatically on first run (`data/assistant.db`). To move to PostgreSQL later, change
`DATABASE_URL` (e.g. `postgresql+psycopg://...`) and `pip install psycopg`; models are plain SQLAlchemy.

## 6-7. Run and open
```
python run.py
```
Open http://127.0.0.1:8000, create an account, then complete the setup wizard.

## 8. Configuring news
Edit `SOURCES` / `DISCOVERY` in `app/services/news_service.py`. Press "Refresh news" or wait for the 3-hourly job.
- Tier 1: official bodies (RBI, SEBI, PIB, Fed, SEC, UN, `.gov` / `.gov.in` domains), trust base 95.
- Tier 2: Reuters, AP, BBC, The Hindu, Indian Express, FT, WSJ, trust base 80.
- Tier 3: Google News discovery. The original publisher is read from each entry and re-classified, so a Reuters story found via Google News is Tier 2, an unknown blog stays Tier 3 and is labelled "Unverified".
- Stories from different sources with similar headlines are clustered; each extra independent source adds trust (+5, max +15) and importance.
- Reuters/AP have no public RSS, so they are reached through discovery queries (`site:reuters.com`).
- Some feed URLs may change over time. A dead feed is logged and skipped; it never breaks the app.

## 9. Configuring reminders
Defaults for new accounts come from `.env`; each user's times and timezone are stored in the database and changed in
Settings (a one-minute scheduler tick reads them, so changes apply immediately). One reminder per habit per day, none if already done; after 3 ignored
reminders it suggests moving the session to the hour you usually complete it.

## 10. Tests
```
python -m pytest
```

## 11. Architecture
```
app/api        thin FastAPI routers (auth, tasks, habits, news, dashboard, assistant, reviews, profile)
app/services   task_service (CRUD, ranking), scoring_service (streaks, scores, analytics), planner_service,
               news_service, ai_service (OpenAI + offline rule parser), workflow_service (morning/evening/weekly/chat),
               notification_service
app/prompts    system / intent / news / coach prompts and the sectioned context builder
app/scheduler  APScheduler: per-user minute tick + shared news job
app/core       config, database, security (auth, sessions, rate limits), tenancy (row-level isolation)
app/models     SQLAlchemy tables
frontend       index.html, style.css, app.js (no build step)
```
Chat flow: message → intent (LLM JSON, or rule parser when offline) → action executed by services → reply generated from
recorded data. Free-form answers get a sectioned context (profile, today, tasks, habits, recent performance, goals, memory, news),
never the whole database.

## Defaults and decisions (ambiguous points)
- Timestamps are stored as naive UTC; task deadlines as naive local time in your timezone. A date-only deadline means 23:59 that day.
- "This week" / "Monthly" habit figures are rolling 7 / 30 day windows.
- Today's unmet habit does not break a streak until the day ends.
- Daily score: productivity (priority-weighted tasks due or completed that day; 70 neutral if nothing was planned), course and LeetCode (minutes/30), consistency (7-day mandatory completion rate), planning (plan generated that day), personal development (extra learning minutes, 30 = 100).
- Plans keep 15% slack, 10 min buffers, and place Course/LeetCode at your reminder times as protected blocks. Capacity drops to 60% after 2+ missed mandatory days out of 3 ("recovery" day).
- Task time estimates are scaled by your measured actual/estimated ratio once 5+ tasks have `actual_minutes`.
- Unfinished past-due tasks are evaluated (keep / reconsider / decide), never auto-moved.
- Weekly review is generated automatically Sunday 21:45 and on demand.
- Memory: say "remember that I prefer evening LeetCode"; view/delete via `GET/DELETE /api/memory`. Nothing else is stored automatically.
- Notifications are in-app (dashboard banner); desktop/email/Telegram delivery can be added in `notification_service.notify`.

## Natural-language examples
`Add finish my project report by Friday` · `Remind me to call John Friday` · `I finished LeetCode for 40 minutes` ·
`I studied React for 45 minutes` · `Show me today's news` · `What's important today?` · `Plan my day` ·
`I couldn't finish my report` · `Move the report to tomorrow` · `What have I been procrastinating on?` · `How am I doing this month?` ·
`Good morning` · `End my day`
