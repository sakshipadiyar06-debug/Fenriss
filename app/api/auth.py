import re
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo
from app.core import security as sec
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.tenancy import TenantMixin
from app.models import AuthSession, User

router = APIRouter(prefix="/api/auth", tags=["auth"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=60)
    timezone: str = Field(default="Asia/Kolkata", max_length=60)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class ChangePwIn(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class DeleteIn(BaseModel):
    password: str = Field(max_length=128)


def public(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "setup_complete": u.setup_complete}


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.allow_registration:
        raise HTTPException(403, "Registration is closed")
    ip = sec.client_ip(request)
    if sec.register_by_ip.blocked(ip):
        raise HTTPException(429, "Too many sign-ups from this address. Try again later.")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address")
    try:
        ZoneInfo(body.timezone)
        tz = body.timezone
    except Exception:
        tz = s.timezone
    sec.register_by_ip.hit(ip)
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    u = User(email=email, password_hash=sec.hash_password(body.password), name=body.name.strip(), timezone=tz,
             morning_time=s.morning_briefing_time, course_time=s.course_reminder_time,
             leetcode_time=s.leetcode_reminder_time, evening_time=s.evening_review_time)
    db.add(u)
    db.commit()
    sec.start_session(db, u, request, response)
    return {"user": public(u)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip = sec.client_ip(request)
    acct = f"{ip}|{email}"
    if sec.login_by_account.blocked(acct) or sec.login_by_ip.blocked(ip):
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    u = db.scalar(select(User).where(User.email == email))
    ok = sec.verify_password(body.password, u.password_hash if u else sec.DUMMY_HASH)
    if not (u and ok and u.is_active):
        sec.login_by_account.hit(acct)
        sec.login_by_ip.hit(ip)
        raise HTTPException(401, "Invalid email or password")
    sec.login_by_account.clear(acct)
    sec.start_session(db, u, request, response)
    return {"user": public(u)}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sec.end_session(db, request, response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(sec.current_user)):
    return {"user": public(user)}


@router.post("/change-password")
def change_password(body: ChangePwIn, request: Request, response: Response, user: User = Depends(sec.current_user),
                    db: Session = Depends(get_db)):
    if not sec.verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    user.password_hash = sec.hash_password(body.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))  # sign out everywhere
    db.commit()
    sec.start_session(db, user, request, response)
    return {"ok": True}


@router.post("/delete-account")
def delete_account(body: DeleteIn, response: Response, user: User = Depends(sec.current_user), db: Session = Depends(get_db)):
    """Permanently erases the account and every row that belongs to it."""
    if not sec.verify_password(body.password, user.password_hash):
        raise HTTPException(403, "Password is incorrect")
    for m in Base.registry.mappers:
        if issubclass(m.class_, TenantMixin):
            db.execute(delete(m.class_).where(m.class_.user_id == user.id))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    response.delete_cookie(sec.COOKIE, path="/")
    return {"deleted": True}
