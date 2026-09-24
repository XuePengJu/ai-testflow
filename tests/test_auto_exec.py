"""M2 执行引擎验收测试（contract-m2 3/4）。

覆盖：
1. 完整执行链路：手工造样例任务目录（1 过 / 1 断言失败 / 1 skip + 最小 conftest）
   → POST run-auto → 轮询到 completed → report_json 的 summary/cases/screenshot 与实际一致
2. 409（running 重复触发 / retry）、400（无用例任务）
3. files 端点：合法截图 200、`..` 穿越 400、白名单外 404、缺失文件 404
4. retry 全流程：trigger=retry 新 run、原 run 不变

样例 conftest 模拟契约 1 的失败截图行为（hook 写 AITF_SHOTS_DIR），不启动真实浏览器
（子进程 pytest 不触碰 page fixture，pytest-playwright 仅作为 --browser 参数接收方）。
"""
import json
import time
import uuid

import pytest

from app.core.config import OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.automation import ExecutionRun
from app.models.task import Task
from app.models.user import User

# ---- 样例脚本内容（模拟 m2-scripter 产物，contract-m2 1） ----

CONFTEST = '''"""样例 conftest：模拟失败截图 hook（真实模板由 m2-scripter 写入）。"""
import os
import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        shots = os.environ.get("AITF_SHOTS_DIR", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
            safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in item.nodeid)
            with open(os.path.join(shots, f"{safe}.png"), "wb") as f:
                f.write(b"\\x89PNG\\r\\n\\x1a\\n" + b"fake-png-bytes")
'''

TESTS = '''"""样例测试脚本：1 过 / 1 断言失败 / 1 skip。"""


def test_tc_001():
    assert 1 + 1 == 2


def test_tc_002():
    assert 1 + 1 == 3, "故意失败的断言"


def test_tc_003():
    import pytest
    pytest.skip("暂不支持")
'''

CASES = json.dumps([
    {"case_id": "TC-001", "title": "登录成功 -> 跳转首页"},
    {"case_id": "TC-002", "title": "错误密码 -> 提示错误"},
    {"case_id": "TC-003", "title": "锁定账户 -> 提示锁定"},
], ensure_ascii=False)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _alice(db) -> User:
    return db.query(User).filter(User.username == "alice").one()


def _insert_task(db, cases_json: str = CASES) -> Task:
    """直接往 DB 插 fake task（不走生成流水线），归属 alice。"""
    task = Task(
        id=uuid.uuid4().hex[:12], name="执行引擎验收任务", kind="e2e",
        source_type="url", input_ref="https://demo.example.com",
        formats="xlsx,json", roles='["qa"]', status="completed",
        user_id=_alice(db).id, cases_json=cases_json,
    )
    db.add(task)
    db.commit()
    return task


def _make_auto_scripts(db, task: Task):
    """任务目录下生成最小 auto/ 脚本集（conftest + mapping + 测试文件）。"""
    auto = OUTPUT_DIR / task.user_data_dir(db) / task.id / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    (auto / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (auto / "mapping.json").write_text(json.dumps({
        "test_tc_001": "TC-001", "test_tc_002": "TC-002", "test_tc_003": "TC-003",
    }), encoding="utf-8")
    (auto / "test_cases_0.py").write_text(TESTS, encoding="utf-8")
    return auto


def _wait_finished(client, token: str, run_id: str, timeout: float = 120) -> dict:
    """轮询执行详情直到终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/executions/{run_id}", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.5)
    raise AssertionError(f"执行 {run_id} 超时未结束")


def test_run_auto_full_flow(client, accounts, db_session):
    """主链路：触发执行 → completed → report_json 与实际一致 + 失败截图真实存在。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    auto = _make_auto_scripts(db_session, task)

    # 触发前 TaskOut 增量：has_auto=true（脚本已生成）
    r = client.get(f"/api/tasks/{task.id}", headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["has_auto"] is True
    assert r.json()["target_id"] is None

    # 触发执行
    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["run_id"]
    run_id = body["run_id"]

    detail = _wait_finished(client, token, run_id)
    assert detail["status"] == "completed", f"执行应 completed，实际: {detail}"
    report = detail["report"]
    assert report, "completed 必须带解析后的 report 对象"

    # summary：3 条用例 1 过 / 1 败 / 1 跳
    s = report["summary"]
    assert (s["total"], s["passed"], s["failed"], s["skipped"]) == (3, 1, 1, 1)
    assert s["duration_ms"] >= 0
    assert report["environment"]["browser"] == "chromium"
    assert report["environment"]["base_url"] == ""  # 无 target 时兜底空串

    # cases：顺序无关按 case_id 索引断言
    by_id = {c["case_id"]: c for c in report["cases"]}
    assert by_id["TC-001"]["outcome"] == "passed"
    assert by_id["TC-001"]["node_id"] == "test_cases_0.py::test_tc_001"
    assert by_id["TC-001"]["title"] == "登录成功 -> 跳转首页"
    assert by_id["TC-002"]["outcome"] == "failed"
    assert "故意失败的断言" in (by_id["TC-002"]["error"] or "")
    assert by_id["TC-003"]["outcome"] == "skipped"
    assert by_id["TC-003"]["error"] is None

    # 失败截图：report 里给了相对路径，文件必须真实存在且可下载
    shot = by_id["TC-002"]["screenshot"]
    assert shot and shot.startswith("shots/"), f"失败用例应带截图: {shot}"
    assert (auto / shot).is_file(), f"截图文件应真实存在: {auto / shot}"

    # run 汇总字段与 report 一致
    assert (detail["passed"], detail["failed"], detail["skipped"]) == (1, 1, 1)
    assert detail["total"] == 3 and detail["progress"] == 3

    # 执行历史列表（新→旧）至少 1 条
    r = client.get(f"/api/tasks/{task.id}/executions", headers=_auth(token))
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [run_id]

    # ---- files 端点安全场景（复用本 run 的真实截图） ----
    r = client.get(f"/api/executions/{run_id}/files/{shot}", headers=_auth(token))
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    # `..` 目录穿越 → 400
    r = client.get(f"/api/executions/{run_id}/files/../conftest.py", headers=_auth(token))
    assert r.status_code in (400, 404)
    r = client.get(f"/api/executions/{run_id}/files/shots%2F..%2F..%2Fconftest.py",
                   headers=_auth(token))
    assert r.status_code in (400, 404)
    # 白名单外前缀 → 404
    r = client.get(f"/api/executions/{run_id}/files/mapping.json", headers=_auth(token))
    assert r.status_code == 404
    # 白名单内但不存在 → 404
    r = client.get(f"/api/executions/{run_id}/files/shots/missing.png", headers=_auth(token))
    assert r.status_code == 404

    # ---- retry：completed 后可重试，新 run trigger=retry，原 run 不变 ----
    r = client.post(f"/api/executions/{run_id}/retry", headers=_auth(token))
    assert r.status_code == 200, r.text
    retry_id = r.json()["run_id"]
    assert retry_id != run_id
    detail2 = _wait_finished(client, token, retry_id)
    assert detail2["status"] == "completed"
    assert detail2["trigger"] == "retry"
    r = client.get(f"/api/executions/{run_id}", headers=_auth(token))
    assert r.json()["trigger"] == "manual" and r.json()["id"] == run_id  # 原 run 未被改


def test_run_auto_409_when_running(client, accounts, db_session):
    """已有 pending/running 执行时重复触发 → 409。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    _make_auto_scripts(db_session, task)
    # 手工插一条 running 记录模拟执行中（稳定复现，不依赖轮询时机）
    running = ExecutionRun(
        id=uuid.uuid4().hex[:12], task_id=task.id, user_id=task.user_id,
        trigger="manual", status="running", total=3,
        auto_dir=f"outputs/{task.user_data_dir(db_session)}/{task.id}/auto",
    )
    db_session.add(running)
    db_session.commit()

    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 409, r.text
    # retry 同理：原 run 运行中 → 409
    r = client.post(f"/api/executions/{running.id}/retry", headers=_auth(token))
    assert r.status_code == 409, r.text

    # 列表接口在 running 态可见（新→旧）
    r = client.get(f"/api/tasks/{task.id}/executions", headers=_auth(token))
    assert [x["status"] for x in r.json()] == ["running"]


def test_run_auto_400_without_cases(client, accounts, db_session):
    """无用例（cases_json 空）任务触发执行 → 400。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session, cases_json="[]")
    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 400, r.text
    # cases_json 为 None 同样 400
    task2 = _insert_task(db_session, cases_json=None)
    r = client.post(f"/api/tasks/{task2.id}/run-auto", headers=_auth(token))
    assert r.status_code == 400, r.text


def test_run_auto_404_other_users_task(client, accounts, db_session):
    """admin 可越权查看，未登录 401；不存在的 run → 404。"""
    token = accounts["user"]["token"]
    admin = accounts["admin"]["token"]
    task = _insert_task(db_session)

    r = client.post(f"/api/tasks/{task.id}/run-auto")  # 未登录
    assert r.status_code == 401
    r = client.get(f"/api/tasks/{task.id}/executions", headers=_auth(admin))
    assert r.status_code == 200  # admin 越权可见
    r = client.get("/api/executions/nonexistent", headers=_auth(token))
    assert r.status_code == 404


def test_has_auto_false_without_scripts(client, accounts, db_session):
    """无 auto 目录的任务 has_auto=false。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    r = client.get(f"/api/tasks/{task.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["has_auto"] is False


def test_failed_run_when_no_scripts(client, accounts, db_session):
    """脚本目录缺失 → 执行 failed（进程级失败），error 有说明。"""
    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = _wait_finished(client, token, r.json()["run_id"])
    assert detail["status"] == "failed"
    assert "脚本目录不存在" in (detail["error"] or "")
    # failed 时不给 report
    assert detail["report"] is None
