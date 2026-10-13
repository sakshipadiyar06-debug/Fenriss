"""Row-level tenant isolation.

Every per-user table inherits TenantMixin (a user_id column). Two session hooks enforce isolation:
  * every SELECT is automatically filtered to the session's user (deny-by-default: no user => no rows)
  * every new row is stamped with the session's user, and writing for another user raises
Sessions are bound to a user by `current_user` (HTTP) or the scheduler. Set session.info["system"]=True
to bypass (never done in request handling).
"""
from sqlalchemy import ForeignKey, Integer, Table, event
from sqlalchemy.sql.selectable import Join
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria


class TenantMixin:
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)


def _tenant_tables():
    from app.core.database import Base
    return {m.local_table for m in Base.registry.mappers if issubclass(m.class_, TenantMixin)}


def _flatten(frm):
    if isinstance(frm, Join):
        yield from _flatten(frm.left)
        yield from _flatten(frm.right)
    elif isinstance(frm, Table):
        yield frm


@event.listens_for(Session, "do_orm_execute")
def _scope_reads(state):
    if not state.is_select or state.session.info.get("system"):
        return
    uid = state.session.info.get("uid", -1)  # -1 matches nothing: unauthenticated sessions see no data
    stmt = state.statement
    tenant = _tenant_tables()
    # 1) explicit WHERE user_id = :uid for every per-user table in the FROM list. This also covers
    #    aggregate-only queries such as select(func.count()).select_from(Task), which entity-level
    #    loader criteria do not reliably touch.
    for frm in stmt.get_final_froms():
        for tbl in _flatten(frm):
            if tbl in tenant:
                stmt = stmt.where(tbl.c.user_id == uid)
    # 2) entity-level criteria as a second layer (covers loader/refresh paths)
    state.statement = stmt.options(with_loader_criteria(TenantMixin, lambda cls: cls.user_id == uid))


@event.listens_for(Session, "before_flush")
def _stamp_writes(session, flush_context, instances):
    uid = session.info.get("uid")
    for obj in session.new:
        if isinstance(obj, TenantMixin):
            if obj.user_id is None:
                if uid is None:
                    raise RuntimeError("Refusing to write tenant data without an authenticated user")
                obj.user_id = uid
            elif uid is not None and obj.user_id != uid and not session.info.get("system"):
                raise RuntimeError("Cross-tenant write blocked")
