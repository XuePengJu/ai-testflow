"""用户 / 访客防滥用计数 / 清理审计 数据模型（V2）。"""
from app.core.utils import utcnow

from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime,
)

from app.core.db import Base


class User(Base):
    """三级角色：guest（按 IP 临时）/ user（注册）/ admin（管理员）。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=True)      # guest 无邮箱
    password_hash = Column(String(128), nullable=True)           # guest 无密码
    role = Column(String(16), default="user", nullable=False)    # guest / user / admin
    ip_hash = Column(String(64), index=True)                     # 仅 guest：同 IP 复用
    expires_at = Column(DateTime, nullable=True)                 # 仅 guest：now + 24h
    data_dir = Column(String(128), default="")                   # u_<id> / guest_<ip_hash>_<seq>
    is_active = Column(Boolean, default=True)                    # 软禁用
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime)


class GuestCreationLog(Base):
    """guest 创建日志（只追加不删）：支撑"单 IP 24h ≤ 5 个"防滥用计数。

    独立于 users 表——guest 记录到期物理删除后，计数仍然有效。
    """
    __tablename__ = "guest_creation_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip_hash = Column(String(64), index=True, nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)


class CleanLog(Base):
    """访客清理审计：guest 用户记录物理删，审计走这张表。"""
    __tablename__ = "clean_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guest_ip_hash = Column(String(64), default="")
    guest_username = Column(String(64), default="")
    deleted_tasks = Column(Integer, default=0)
    deleted_files = Column(Integer, default=0)
    trigger = Column(String(16), default="")   # scheduler / manual / lazy / disable
    cleaned_at = Column(DateTime, default=utcnow, index=True)
