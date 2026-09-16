"""用户 / 访客防滥用计数 / 清理审计 数据模型（V2）。"""
from datetime import datetime

from app.core.utils import utcnow

from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    """三级角色：guest（按 IP 临时）/ user（注册）/ admin（管理员）。"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_dir: Mapped[str | None] = mapped_column(String(128), default="")
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class GuestCreationLog(Base):
    """guest 创建日志（只追加不删）：支撑"单 IP 24h ≤ 5 个"防滥用计数。

    独立于 users 表——guest 记录到期物理删除后，计数仍然有效。
    """
    __tablename__ = "guest_creation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, index=True)


class CleanLog(Base):
    """访客清理审计：guest 用户记录物理删，审计走这张表。"""
    __tablename__ = "clean_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guest_ip_hash: Mapped[str | None] = mapped_column(String(64), default="")
    guest_username: Mapped[str | None] = mapped_column(String(64), default="")
    deleted_tasks: Mapped[int | None] = mapped_column(Integer, default=0)
    deleted_files: Mapped[int | None] = mapped_column(Integer, default=0)
    trigger: Mapped[str | None] = mapped_column(String(16), default="")
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow, index=True)
