"""M1 全链路测试：被测系统（TestTarget）数据模型 + 登录凭据 Fernet 加密。

凭据加密约定（对齐设计文档 3.1）：
- 密钥由 JWT_SECRET 经 HKDF 确定性派生（不落库、免迁移）
- username / password 加密后落 username_enc / password_enc 列，API 永不回传明文
"""
import base64
from datetime import datetime

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String, Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow

# 派生盐/信息串与 API 响应加密（app/core/crypto.py）区分开，互不影响
_KDF_SALT = b"aitf-target-cred-v1"
_KDF_INFO = b"aitf:target-cred"

_FERNET = None


def _get_fernet():
    """懒加载 Fernet 实例（密钥派生自 JWT_SECRET，进程内缓存）。"""
    global _FERNET
    if _FERNET is None:
        from cryptography.fernet import Fernet

        from app.core import config

        raw = HKDF(
            algorithm=hashes.SHA256(), length=32,
            salt=_KDF_SALT, info=_KDF_INFO,
        ).derive(config.JWT_SECRET.encode("utf-8"))
        _FERNET = Fernet(base64.urlsafe_b64encode(raw))
    return _FERNET


def encrypt_credential(plain: str) -> str:
    """明文凭据 → Fernet token（str）。空串原样返回 None 由调用方处理。"""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_credential(token: str | None) -> str:
    """Fernet token → 明文。空/解密失败返回空串（不让脏数据把任务打崩）。"""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001  密钥轮换/脏数据场景兜底
        return ""


class TestTarget(Base):
    """被测系统：一个 URL（+ 可选登录凭据），供 e2e 全链路任务引用。"""
    __tablename__ = "test_targets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="未命名系统")
    base_url: Mapped[str | None] = mapped_column(String(512), default="")
    # 登录方式：none / form（M1 仅表单登录，预留 basic 等扩展）
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    username_enc: Mapped[str | None] = mapped_column(Text, default=None)
    password_enc: Mapped[str | None] = mapped_column(Text, default=None)
    # 最近一次抓取的页面结构缓存（PageDesc 列表 JSON，供后续复用/排查）
    pages_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)


class ExecutionRun(Base):
    """M2 自动化执行记录：一轮 pytest 全链路执行（V5.0 3.1 / contract-m2 2）。

    状态机：pending → running → completed | failed
    - completed：pytest 正常跑完（有用例失败也算 completed，报告如实呈现）
    - failed：仅进程级崩溃 / 超时 / 报告文件缺失
    report_json 存组装后的 JSON 文本，结构见 contract-m2 2（summary/cases/environment）。
    """
    __tablename__ = "execution_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 逻辑外键 tasks.id：不加物理 FK（与 Task.target_id/user_id 等先例一致）。
    # 原因：MySQL 5.7 下 tasks 表 collation（utf8mb4_general_ci）与库默认
    # （utf8mb4_unicode_ci）不一致，物理 FK 建表报 1215；且物理 FK 跨字符集/双方言
    # 维护成本高，属主完整性由 API 层 _own_task 校验保证。
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 触发方式：manual（点执行）/ retry（失败重试，新 run 不改原 run）
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 组装后的报告 JSON 文本（summary + cases + environment），终态才有
    report_json: Mapped[str | None] = mapped_column(Text, default=None)
    # 脚本目录相对路径：outputs/{user_data_dir}/{task_id}/auto（相对 AITF_ROOT_DIR）
    auto_dir: Mapped[str | None] = mapped_column(String(512), default="")
    # failed 时的进程级错误（stderr 尾部 / 超时说明）；用例断言失败不写这里
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # M4 自愈循环（V5.0 4.2）：已进行的自愈轮次（0=未触发自愈）
    heal_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 自愈过程 JSON 数组文本，每轮一条：
    # {round, suspects:[{case_id, error, screenshot}], diagnosis, changed_files:[], rerun_outcome}
    heal_log: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
