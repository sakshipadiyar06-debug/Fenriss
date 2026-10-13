from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import current_user
from app.models import Notification, User
from app.services import workflow_service as wf

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = wf.dashboard(db, user)
    d["notifications"] = [{"id": n.id, "kind": n.kind, "message": n.message[:400], "created_at": n.created_at.isoformat()}
                          for n in db.scalars(select(Notification).where(Notification.read == False)  # noqa: E712
                                              .order_by(Notification.id.desc()).limit(5))]
    return d


@router.get("/analytics")
def analytics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return wf.analytics(db, user)
