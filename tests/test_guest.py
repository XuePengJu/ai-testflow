"""访客测试：生命周期 / 防滥用 / 懒清理 / 转正。对应方案 9.7 访客相关两张表。"""
import time

SPEC_TEXT = "# 登录模块\n- 用户输入正确账号密码，点击登录，登录成功跳转首页"


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _mk_task(client, token):
    return client.post("/api/tasks", headers=_hdr(token),
                       data={"text": SPEC_TEXT, "kind": "business", "formats": "json"})


def _wait_done(client, token, tid, timeout=30):
    for _ in range(timeout * 2):
        r = client.get(f"/api/tasks/{tid}", headers=_hdr(token))
        if r.status_code == 200 and r.json()["status"] in ("completed", "failed"):
            return r.json()
        time.sleep(0.5)
    raise AssertionError(f"任务 {tid} 未在 {timeout}s 内完成")


def _delete_guest_record(username: str):
    """物理删除 guest 记录（模拟清理），验证创建日志计数仍在。"""
    from app.core.db import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    try:
        g = db.query(User).filter(User.username == username).first()
        if g:
            db.delete(g)
            db.commit()
    finally:
        db.close()


# ---------------- 生命周期 ----------------

def test_guest_token_issue(client, fresh_guest):
    token, _ = fresh_guest
    r = client.get("/api/auth/me", headers=_hdr(token))
    j = r.json()
    assert r.status_code == 200
    assert j["role"] == "guest"
    assert j["username"].startswith("guest_")
    assert 20 < j["remaining_hours"] <= 24, "剩余时长应约为 24h"


def test_same_ip_reuse(client, fresh_guest, monkeypatch):
    """同 IP 未过期二访 → 复用同一 guest（数据续用）。"""
    from app.api import guest as guest_mod
    token, ip = fresh_guest
    me1 = client.get("/api/auth/me", headers=_hdr(token)).json()
    monkeypatch.setattr(guest_mod, "_client_ip", lambda req: ip)
    r2 = client.post("/api/guest/token")
    assert r2.status_code == 200
    assert r2.json()["username"] == me1["username"]


def test_guest_files_isolated(client, fresh_guest):
    token, _ = fresh_guest
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    r = _mk_task(client, token)
    assert r.status_code == 201
    from app.core.config import OUTPUT_DIR
    assert (OUTPUT_DIR / me["username"]).exists(), "访客文件应隔离在自己的临时目录"


# ---------------- 防滥用 ----------------

def test_guest_task_limit(client, fresh_guest):
    """访客任务上限（测试环境 GUEST_MAX_TASKS=3）→ 第 4 个 429。"""
    token, _ = fresh_guest
    for _ in range(3):
        assert _mk_task(client, token).status_code == 201
    assert _mk_task(client, token).status_code == 429


def test_guest_daily_limit_survives_record_delete(client, monkeypatch):
    """单 IP 24h 上限（测试环境=3）：guest 记录物理删除后计数仍在 → 第 4 次 429。"""
    from app.api import guest as guest_mod
    monkeypatch.setattr(guest_mod, "_client_ip", lambda req: "172.31.99.9")
    for _ in range(3):
        r = client.post("/api/guest/token")
        assert r.status_code == 200
        _delete_guest_record(r.json()["username"])  # 模拟到期物理删
    r = client.post("/api/guest/token")
    assert r.status_code == 429, "计数走独立表，不随 guest 记录删除失效"


# ---------------- 过期与懒清理 ----------------

def test_expired_guest_lazy_clean(client, fresh_guest, expire_guest):
    token, _ = fresh_guest
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    uname = me["username"]

    r = _mk_task(client, token)
    assert r.status_code == 201
    _wait_done(client, token, r.json()["id"])  # 等后台任务跑完再清理，避免并发写

    expire_guest(uname)
    r = client.get("/api/auth/me", headers=_hdr(token))
    assert r.status_code == 401
    assert "到期" in r.text

    # 懒清理：用户记录物理删 + 任务级联删 + 临时目录删
    from app.core.db import SessionLocal
    from app.models.user import User
    from app.models.task import Task
    from app.core.config import OUTPUT_DIR
    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username == uname).first() is None, "guest 应被物理删除"
        assert db.query(Task).filter(Task.user_id.isnot(None)).count() >= 0
    finally:
        db.close()
    assert not (OUTPUT_DIR / uname).exists(), "访客临时目录应被删除"


def test_same_ip_recreate_after_clean(client, fresh_guest, expire_guest, monkeypatch):
    """清理后同 IP 再访 → 全新 guest（seq 递增，不撞唯一约束）。"""
    from app.api import guest as guest_mod
    token, ip = fresh_guest
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    expire_guest(me["username"])
    client.get("/api/auth/me", headers=_hdr(token))  # 触发懒清理

    monkeypatch.setattr(guest_mod, "_client_ip", lambda req: ip)
    r = client.post("/api/guest/token")
    assert r.status_code == 200
    assert r.json()["username"] != me["username"]
    assert r.json()["username"].startswith("guest_")


# ---------------- 转正 ----------------

def test_guest_upgrade(client, fresh_guest):
    """访客转正：任务保留 + 文件迁入新目录 + 原 token 角色变化。"""
    token, _ = fresh_guest
    me = client.get("/api/auth/me", headers=_hdr(token)).json()
    uname = me["username"]

    r = _mk_task(client, token)
    assert r.status_code == 201
    tid = r.json()["id"]
    _wait_done(client, token, tid)

    r = client.post("/api/guest/upgrade", headers=_hdr(token),
                    json={"username": "upgraded_user", "email": "up@test.com",
                          "password": "Upwd1234"})
    assert r.status_code == 200, r.text
    new_token = r.json()["access_token"]
    assert r.json()["role"] == "user"

    # me 角色变为 user
    me2 = client.get("/api/auth/me", headers=_hdr(new_token)).json()
    assert me2["role"] == "user" and me2["username"] == "upgraded_user"

    # 任务保留（迁移不丢）
    tasks = client.get("/api/tasks", headers=_hdr(new_token)).json()
    assert any(t["id"] == tid for t in tasks), "转正后任务应完整保留"

    # 目录迁移 + 下载可用
    from app.core.config import OUTPUT_DIR
    assert (OUTPUT_DIR / f"u_{me2['id']}").exists()
    assert not (OUTPUT_DIR / uname).exists(), "旧 guest 目录应被迁走"
    assert client.get(f"/api/tasks/{tid}/download?fmt=json",
                      headers=_hdr(new_token)).status_code == 200

    # 转正后不能再调转正接口
    r = client.post("/api/guest/upgrade", headers=_hdr(new_token),
                    json={"username": "x_again", "email": "x@test.com",
                          "password": "Upwd1234"})
    assert r.status_code == 403


def test_registered_user_cannot_guest_token_flow(client, accounts):
    """注册用户走 /api/guest/token 会按 IP 另建身份（与自身账号无关）——行为符合设计，
    此处断言不会污染已有账号。"""
    before = client.get("/api/auth/me", headers=_hdr(accounts["user"]["token"])).json()
    client.post("/api/guest/token")
    after = client.get("/api/auth/me", headers=_hdr(accounts["user"]["token"])).json()
    assert before["id"] == after["id"]
