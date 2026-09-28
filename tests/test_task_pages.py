"""页面探索可视化端点测试（GET /api/tasks/{id}/pages*）。

覆盖：
1. 有 pages.json → 200 且 pages 内容原样返回
2. 无 pages.json（未跑过 crawler / 旧任务）→ 200 空列表
3. 截图端点：合法文件名 200、`..%2F` 穿越类文件名 400、文件缺失 404
4. 他人任务 → 404（不暴露存在性）
"""
import json
import uuid

from app.core.config import OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.task import Task
from app.models.user import User

# 最小 PageDesc 样例（与 web_crawler.crawl_pages_json 落盘字段对齐）
PAGES = [
    {
        "url": "https://demo.example.com/",
        "title": "首页",
        "headings": ["欢迎"],
        "forms": [],
        "buttons": ["登录"],
        "links": ["https://demo.example.com/login"],
        "nav_texts": ["首页", "关于"],
        "text_digest": "示例站点首页文本",
        "rendered": True,
        "screenshot": "pages/page-001.png",
    },
    {
        "url": "https://demo.example.com/login",
        "title": "登录",
        "headings": [],
        "forms": [{"action": "/login", "method": "post", "fields": []}],
        "buttons": [],
        "links": [],
        "nav_texts": [],
        "text_digest": "",
        "rendered": False,
        "screenshot": "",
    },
]

# 1x1 透明 PNG（最小合法图片字节，FileResponse 仅回传不校验内容，够用）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"

# 伪 webm（EBML 魔数开头即可，FileResponse 只透传字节不校验容器）
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"fake-webm-payload" * 8


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _alice(db) -> User:
    return db.query(User).filter(User.username == "alice").one()


def _bob_token(client) -> str:
    """再注册一个普通用户 bob，用于「他人任务 404」用例。"""
    client.post("/api/auth/register", json={
        "username": "bob_pages", "email": "bob_pages@test.com", "password": "Bob1234x"})
    r = client.post("/api/auth/login", data={"username": "bob_pages", "password": "Bob1234x"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _insert_task(db, user: User) -> Task:
    """直接往 DB 插 fake e2e 任务（不走生成流水线）。"""
    task = Task(
        id=uuid.uuid4().hex[:12], name="页面探索测试任务", kind="e2e",
        source_type="url", input_ref="https://demo.example.com",
        formats="xlsx,json", roles='["qa"]', status="completed",
        user_id=user.id, cases_json="[]",
    )
    db.add(task)
    db.commit()
    return task


def _make_pages_artifacts(db, task: Task, with_pages: bool = True) -> None:
    """任务输出目录手写 crawler 产物：pages.json + pages/page-001.png。"""
    out = OUTPUT_DIR / task.user_data_dir(db) / task.id
    out.mkdir(parents=True, exist_ok=True)
    if with_pages:
        (out / "pages.json").write_text(json.dumps(PAGES, ensure_ascii=False), encoding="utf-8")
        pages_dir = out / "pages"
        pages_dir.mkdir(exist_ok=True)
        (pages_dir / "page-001.png").write_bytes(PNG_BYTES)


def test_pages_with_artifacts(client, accounts, db_session):
    """有 pages.json：200 且 pages 与落盘内容一致；无录屏时 video_available=False。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session, _alice(db_session))
    _make_pages_artifacts(db_session, task)

    r = client.get(f"/api/tasks/{task.id}/pages", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"] == task.id
    assert len(body["pages"]) == 2
    assert body["pages"][0]["title"] == "首页"
    assert body["pages"][0]["screenshot"] == "pages/page-001.png"
    assert body["video_available"] is False


def test_pages_without_artifacts(client, accounts, db_session):
    """无 pages.json：200 + 空列表 + 无录屏标记（前端据此隐藏区块）。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session, _alice(db_session))

    r = client.get(f"/api/tasks/{task.id}/pages", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == {"task_id": task.id, "pages": [], "video_available": False}


def test_pages_forbidden_for_other_user(client, accounts, db_session):
    """他人任务 → 404（与任务接口不暴露存在性策略一致）。"""
    alice_token = accounts["user"]["token"]
    db = db_session
    task = _insert_task(db, _alice(db))

    r = client.get(f"/api/tasks/{task.id}/pages", headers=_auth(alice_token))
    assert r.status_code == 200

    bob_token = _bob_token(client)
    r = client.get(f"/api/tasks/{task.id}/pages", headers=_auth(bob_token))
    assert r.status_code == 404, r.text
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/page-001.png",
                   headers=_auth(bob_token))
    assert r.status_code == 404, r.text


def test_page_screenshot_ok_and_traversal(client, accounts, db_session):
    """截图端点：合法文件名 200；穿越/非法文件名 400；缺失文件 404。"""
    token = accounts["user"]["token"]
    db = db_session
    task = _insert_task(db, _alice(db))
    _make_pages_artifacts(db, task)

    # 合法：200 + PNG 字节
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/page-001.png",
                   headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.content == PNG_BYTES

    # 文件名合法但截图不存在（page-002 未截图）→ 404
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/page-002.png",
                   headers=_auth(token))
    assert r.status_code == 404, r.text

    # 白名单外文件名 → 400（非序号命名 / 非png 均拒绝）
    for bad in ("evil.png", "page-abc.png", "page-001.txt", "page-001.PNG"):
        r = client.get(f"/api/tasks/{task.id}/pages/screenshot/{bad}",
                       headers=_auth(token))
        assert r.status_code == 400, f"{bad} 应为 400，实际 {r.status_code}"

    # 穿越类（含 %2F）：路由层多段不匹配 404 / 处理器白名单 400，均不允许读文件
    # （纯 ../ 写法会被 httpx 客户端 URL 规范化，服务端收到的已是合法名，故只测编码写法）
    for bad in ("..%2F..%2Fapp.db", "page-001.png%2F..%2F..%2Fsecret"):
        r = client.get(f"/api/tasks/{task.id}/pages/screenshot/{bad}",
                       headers=_auth(token))
        assert r.status_code in (400, 404), \
            f"{bad} 应被拒绝，实际 {r.status_code}"
        assert r.status_code != 200


def test_explore_step_screenshot_routed(client, accounts, db_session):
    """M5 探索步骤截图：step-NNN.png 白名单内、按前缀路由到 explore/ 子目录。"""
    token = accounts["user"]["token"]
    db = db_session
    task = _insert_task(db, _alice(db))
    out = OUTPUT_DIR / task.user_data_dir(db) / task.id
    edir = out / "explore"
    edir.mkdir(parents=True, exist_ok=True)
    (edir / "step-001.png").write_bytes(PNG_BYTES)

    # 合法：200 + PNG 字节（explore/ 子目录）
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/step-001.png",
                   headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.content == PNG_BYTES

    # explore/ 下无 page-NNN.png（page 前缀仍路由 pages/）→ 404
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/page-001.png",
                   headers=_auth(token))
    assert r.status_code == 404, r.text

    # 前缀+序号组合外仍拒绝（跨前缀不存在跨目录读取）
    r = client.get(f"/api/tasks/{task.id}/pages/screenshot/step-../x.png",
                   headers=_auth(token))
    assert r.status_code in (400, 404)


def _make_video_artifact(db, task: Task) -> None:
    """任务输出目录手写探索录屏产物：videos/explore.webm。"""
    out = OUTPUT_DIR / task.user_data_dir(db) / task.id
    vdir = out / "videos"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "explore.webm").write_bytes(WEBM_BYTES)


def test_task_video_ok_missing_forbidden(client, accounts, db_session):
    """探索录屏端点：存在 200（webm 字节透传）/ 缺失 404 / 他人任务 404。"""
    token = accounts["user"]["token"]
    db = db_session
    task = _insert_task(db, _alice(db))
    _make_video_artifact(db, task)

    # 存在：200 + webm 字节
    r = client.get(f"/api/tasks/{task.id}/video", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.content == WEBM_BYTES

    # 缺失（未跑过录屏的任务）→ 404
    task2 = _insert_task(db, _alice(db))
    r = client.get(f"/api/tasks/{task2.id}/video", headers=_auth(token))
    assert r.status_code == 404, r.text

    # 他人任务 → 404（不暴露存在性）
    bob_token = _bob_token(client)
    r = client.get(f"/api/tasks/{task.id}/video", headers=_auth(bob_token))
    assert r.status_code == 404, r.text


def test_pages_video_available_flag(client, accounts, db_session):
    """pages 响应的 video_available 随录屏产物有无切换。"""
    token = accounts["user"]["token"]
    db = db_session
    task = _insert_task(db, _alice(db))
    _make_pages_artifacts(db, task)
    _make_video_artifact(db, task)

    r = client.get(f"/api/tasks/{task.id}/pages", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["video_available"] is True
