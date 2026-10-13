"""Passwords, sessions, rate limiting and the current_user dependency."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.database import get_db
from app.core.timeutil import utcnow
from app.models import AuthSession, User

COOKIE = "fenris_session"
ITERATIONS = 600_000


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


DUMMY_HASH = hash_password("timing-equaliser")  # verified when the email is unknown, so timing doesn't leak accounts


class RateLimiter:
    """In-memory sliding window (per process; resets on restart)."""
    def __init__(self, limit: int, window: int):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def _prune(self, key):
        q = self.hits.get(key)
        if q is None:
            return None
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self.hits.pop(key, None)
            return None
        return q

    def blocked(self, key) -> bool:
        q = self._prune(key)
        return bool(q) and len(q) >= self.limit

    def hit(self, key):
        self._prune(key)
        self.hits[key].append(time.monotonic())

    def clear(self, key):
        self.hits.pop(key, None)

    def reset(self):
        self.hits.clear()


login_by_account = RateLimiter(8, 900)
login_by_ip = RateLimiter(40, 900)
register_by_ip = RateLimiter(10, 3600)


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()  # rightmost = added by our own proxy
    return request.client.host if request.client else "unknown"


def _hash_token(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def start_session(db: Session, user: User, request: Request, response: Response):
    s = get_settings()
    db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))  # housekeeping
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=_hash_token(token), expires_at=utcnow() + timedelta(days=s.session_days)))
    db.commit()
    secure = s.cookie_secure or request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="lax", secure=secure, path="/")


def end_session(db: Session, request: Request, response: Response):
    tok = request.cookies.get(COOKIE)
    if tok:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _hash_token(tok)))
        db.commit()
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Authenticate the cookie and bind this DB session to the user (enables row-level isolation)."""
    tok = request.cookies.get(COOKIE)
    if not tok:
        raise HTTPException(401, "Not authenticated")
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash_token(tok), AuthSession.expires_at > utcnow()))
    user = db.get(User, sess.user_id) if sess else None
    if not user or not user.is_active:
        raise HTTPException(401, "Not authenticated")
    db.info["uid"] = user.id
    return user
