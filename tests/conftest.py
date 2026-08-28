"""测试夹具：临时数据根目录 + TestClient + 基础账号。

关键：AITF_ROOT_DIR / GUEST_* 等环境变量必须在 import app 之前设置，
config 在模块导入时读取一次。
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path

# ---- 必须在 import app 之前 ----
_TMP_ROOT = tempfile.mkdtemp(prefix="aitf_test_")
os.environ["AITF_ROOT_DIR"] = _TMP_ROOT          # 数据落临时目录，不污染真实 app.db
os.environ["JWT_SECRET"] = "test-secret-for-pytest"
os.environ["ENV"] = "dev"
os.environ["DASHSCOPE_API_KEY"] = ""             # 强制 mock 兜底
os.environ["GUEST_MAX_TASKS"] = "3"              # 调小上限加速用例
os.environ["GUEST_DAILY_LIMIT"] = "3"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402  此时 config 已锁定到临时目录
from app.core.db import SessionLocal  # noqa: E402
from app.models.user import User, GuestCreationLog  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """with 语法触发 lifespan（init_db + 清理调度器启动）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session", autouse=True)
def accounts(client):
    """首个注册用户 = admin（引导规则）；第二个 = 普通用户。

    autouse 保证在任何其他注册发生之前完成，角色判定不被打乱。
    """
    r = client.post("/api/auth/register", json={
        "username": "boss_admin", "email": "admin@test.com", "password": "Admin1234"})
    assert r.status_code == 201 and r.json()["role"] == "admin", r.text

    r = client.post("/api/auth/register", json={
        "username": "alice", "email": "alice@test.com", "password": "Alice1234"})
    assert r.status_code == 201 and r.json()["role"] == "user", r.text

    def _login(u, p):
        r = client.post("/api/auth/login", data={"username": u, "password": p})
        assert r.status_code == 200, r.text
        return r.json()["access_token"]

    return {
        "admin": {"username": "boss_admin", "password": "Admin1234", "token": _login("boss_admin", "Admin1234")},
        "user": {"username": "alice", "password": "Alice1234", "token": _login("alice", "Alice1234")},
    }


@pytest.fixture()
def fresh_guest(client, monkeypatch):
    """唯一 IP 的干净访客（避免用例间串扰）。返回 (token, ip)。"""
    from app.api import guest as guest_mod
    ip = f"10.{uuid.uuid4().int % 200}.{uuid.uuid4().int % 200}.{uuid.uuid4().int % 200}"
    monkeypatch.setattr(guest_mod, "_client_ip", lambda req: ip)
    r = client.post("/api/guest/token")
    assert r.status_code == 200, r.text
    return r.json()["access_token"], ip


@pytest.fixture()
def expire_guest():
    """把指定 username 的 guest 置为过期（直改 DB 模拟 TTL 到期）。"""
    from datetime import timedelta
    from app.core.utils import utcnow

    def _expire(username: str):
        db = SessionLocal()
        try:
            g = db.query(User).filter(User.username == username).first()
            assert g, f"guest {username} 不存在"
            g.expires_at = utcnow() - timedelta(hours=1)
            db.commit()
        finally:
            db.close()
    return _expire


@pytest.fixture()
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def guest_by_username(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()


def delete_guest_record(username: str):
    """物理删除 guest 记录（模拟清理后再测同 IP 重建计数）。"""
    db = SessionLocal()
    try:
        g = db.query(User).filter(User.username == username).first()
        if g:
            db.delete(g)
            db.commit()
    finally:
        db.close()
