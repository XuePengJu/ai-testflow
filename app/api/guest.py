"""访客身份：免注册按 IP 建/复用 + 防滥用 + 一键转正。"""
import hashlib
from datetime import timedelta

from app.core.utils import utcnow

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.deps import require_guest
from app.core import config, crypto, security
from app.core.db import get_db
from app.models.user import User, GuestCreationLog
from app.models.task import Task
from app.jobs.guest_cleaner import clean_guest

router = APIRouter(prefix="/guest", tags=["访客"])


def _client_ip(request: Request) -> str:
    """IP 信任边界：仅自管反代后才开 --proxy-headers 解析 XFF；
    直连/本地一律 request.client.host（TCP 真实来源，伪造不了）。"""
    return request.client.host if request.client else "unknown"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


class UpgradeIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=1)


@router.post("/token")
def guest_token(request: Request, db: Session = Depends(get_db)):
    """按 IP 建/复用访客身份，返回 guest JWT + 剩余有效时长。"""
    ip_hash = _ip_hash(_client_ip(request))

    # 1) 未过期 guest 直接复用（数据续用）
    guest = db.query(User).filter(
        User.ip_hash == ip_hash, User.role == "guest",
        User.expires_at > utcnow(),
    ).first()
    if guest:
        remaining = (guest.expires_at - utcnow()).total_seconds() / 3600
        token = security.create_token(guest.id, guest.username, "guest", ttl_hours=int(remaining) + 1)
        return {"access_token": token, "token_type": "bearer", "role": "guest",
                "username": guest.username, "remaining_hours": round(remaining, 2),
                "enc_key": crypto.derive_key(guest.id)}

    # 2) 防滥用：24h 窗口创建计数（独立表，guest 删除后计数仍在）
    recent = db.query(GuestCreationLog).filter(
        GuestCreationLog.ip_hash == ip_hash,
        GuestCreationLog.created_at >= utcnow() - timedelta(hours=24),
    ).count()
    if recent >= config.GUEST_DAILY_LIMIT:
        raise HTTPException(status_code=429,
                            detail="该 IP 今日访客体验次数已用完，请注册账号")

    # 3) 新建 guest：username 带 seq 防撞唯一约束（记录物理删也不冲突）
    seq = db.query(GuestCreationLog).filter(GuestCreationLog.ip_hash == ip_hash).count()
    expires_at = utcnow() + timedelta(hours=config.GUEST_TTL_HOURS)
    guest = User(
        username=f"guest_{ip_hash}_{seq}", role="guest",
        ip_hash=ip_hash, expires_at=expires_at,
        data_dir=f"guest_{ip_hash}_{seq}",
    )
    db.add(guest)
    db.add(GuestCreationLog(ip_hash=ip_hash))
    db.commit()
    db.refresh(guest)

    token = security.create_token(guest.id, guest.username, "guest",
                                  ttl_hours=config.GUEST_TTL_HOURS)
    return {"access_token": token, "token_type": "bearer", "role": "guest",
            "username": guest.username, "remaining_hours": float(config.GUEST_TTL_HOURS),
            "enc_key": crypto.derive_key(guest.id)}


@router.post("/upgrade")
def guest_upgrade(body: UpgradeIn,
                  guest: User = Depends(require_guest),
                  db: Session = Depends(get_db)):
    """访客转注册用户：任务与文件迁入新账户，数据不丢。"""
    # 密码强度校验（与注册一致）
    from app.api.auth import _validate_password
    _validate_password(body.password)
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="邮箱已被注册")

    # 原子迁移：guest 记录原地转正，保留 id/任务/文件，目录改名
    old_dir = guest.data_dir
    guest.username = body.username
    guest.email = body.email
    guest.password_hash = security.hash_password(body.password)
    guest.role = "user"
    guest.ip_hash = None
    guest.expires_at = None
    guest.data_dir = f"u_{guest.id}"
    guest.last_login_at = utcnow()
    db.commit()

    # 文件迁移：uploads/outputs 下 guest 目录 → u_<id>/
    moved = 0
    if old_dir and old_dir != guest.data_dir:
        import shutil
        from app.core.config import UPLOAD_DIR, OUTPUT_DIR
        for base in (UPLOAD_DIR, OUTPUT_DIR):
            src, dst = base / old_dir, base / guest.data_dir
            if src.exists():
                dst.mkdir(parents=True, exist_ok=True)
                for f in src.iterdir():
                    shutil.move(str(f), str(dst / f.name))
                    moved += 1
                src.rmdir()

    token = security.create_token(guest.id, guest.username, "user")
    # 转正后 user_id 不变 → 派生密钥不变，前端无需换密钥
    return {"access_token": token, "token_type": "bearer", "role": "user",
            "username": guest.username, "moved_files": moved,
            "enc_key": crypto.derive_key(guest.id)}
