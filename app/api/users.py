"""admin 用户管理与治理接口。"""
import shutil
from datetime import datetime, timedelta

from app.core.utils import utcnow

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import get_db
from app.models.task import Task, StepLog
from app.models.user import User, CleanLog
from app.jobs.guest_cleaner import clean_expired, clean_guest

router = APIRouter(tags=["管理"])


class UserPatch(BaseModel):
    is_active: bool | None = None


class UserRow(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    expires_at: datetime | None
    tasks: int
    created_at: datetime | None


@router.get("/users", response_model=list[UserRow])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = []
    for u in db.query(User).order_by(User.id).all():
        n = db.query(Task).filter(Task.user_id == u.id).count()
        rows.append(UserRow(id=u.id, username=u.username, email=u.email, role=u.role,
                            is_active=u.is_active, expires_at=u.expires_at,
                            tasks=n, created_at=u.created_at))
    return rows


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch,
               admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="不能操作自己的账号")

    if u.role == "guest":
        # 禁用 guest = 立即清理其数据（不走软禁用）
        if body.is_active is False:
            stat = clean_guest(db, u, trigger="disable")
            return {"ok": True, "cleaned": stat}
        raise HTTPException(status_code=400, detail="访客仅支持禁用（即清理）")

    if body.is_active is not None:
        u.is_active = body.is_active
        db.commit()
    return {"ok": True, "user_id": u.id, "is_active": u.is_active}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="管理员不可删除自己")

    # 级联：step_logs → tasks → 文件目录 → 用户
    task_ids = [t.id for t in db.query(Task.id).filter(Task.user_id == u.id).all()]
    if task_ids:
        db.query(StepLog).filter(StepLog.task_id.in_(task_ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.user_id == u.id).delete(synchronize_session=False)
    if u.data_dir:
        for base in (UPLOAD_DIR, OUTPUT_DIR):
            shutil.rmtree(base / u.data_dir, ignore_errors=True)
    db.delete(u)
    db.commit()
    return {"ok": True, "deleted_user_id": user_id, "deleted_tasks": len(task_ids)}


@router.post("/admin/guests/clean")
def manual_clean(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    stats = clean_expired(db, trigger="manual")
    return {"cleaned": len(stats), "detail": stats}


@router.post("/admin/guests/clean-all")
def clean_all_guests(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """强制清理全部活跃访客（无论是否到期）——管理员主动批量清理入口。"""
    guests = db.query(User).filter(User.role == "guest").all()
    stats = [clean_guest(db, g, trigger="force") for g in guests]
    return {"cleaned": len(stats), "detail": stats}


@router.get("/admin/stats")
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    now = utcnow()
    return {
        "registered_users": db.query(User).filter(User.role != "guest").count(),
        "active_guests": db.query(User).filter(
            User.role == "guest", User.expires_at > now).count(),
        "cleaned_24h": db.query(CleanLog).filter(
            CleanLog.cleaned_at >= now - timedelta(hours=24)).count(),
        "total_tasks": db.query(Task).count(),
    }
