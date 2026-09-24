"""M4 自愈循环验收测试（V5.0 4.3/4.5）。

覆盖：
1. selector 类：LLM 返回修复 JSON → 文件落盘 + heal_backup 备份存在 + 只重跑失败 node id
   + 重跑结果并入 report_json（heal.rounds/heal.log）
2. product_bug 类：不改任何文件、suspected_bugs 进 report_json、一轮即收敛
3. 断言保护：LLM 返回试图修改断言的修复 → 拒绝写回，文件原样
4. 轮次耗尽：修复无效 → 跑满 AUTO_HEAL_ROUNDS 轮后收敛，suspected_bugs 兜底记录
5. 断言保护校验函数单测（代码级防线本身）

LLM 用 FakeLLM(monkeypatch auto_runner._resolve_heal_client) 固定返回，不真实调用；
pytest 子进程为真实执行（样例脚本不触碰浏览器）。
"""
import json
import uuid

import pytest

from app.core import config
from app.core.config import OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.automation import ExecutionRun

from test_auto_exec import _insert_task, _wait_finished, _auth  # 复用基线夹具（pytest 顶层模块）

# ---- 样例脚本（自愈场景：选择器失效为超时异常，非断言失败） ----

CONFTEST_MIN = '"""最小 conftest：不触碰浏览器。"""\n'

TESTS_HEAL = '''"""自愈样例脚本：1 过 / 1 选择器失效（超时异常）/ 1 skip。"""


def test_tc_001():
    assert 1 + 1 == 2


def test_tc_002():
    locator = "#login-btn-missing"  # 故意写错的选择器（模拟页面改版）
    if locator == "#login-btn-missing":
        raise TimeoutError(f"等待元素 {locator} 超时")
    assert locator == "#login-btn"


def test_tc_003():
    import pytest
    pytest.skip("暂不支持")
'''

# LLM 返回的修复：选择器改正，断言原样保留
TESTS_HEAL_FIXED = '''"""自愈样例脚本：1 过 / 1 选择器失效（超时异常）/ 1 skip。"""


def test_tc_001():
    assert 1 + 1 == 2


def test_tc_002():
    locator = "#login-btn"  # 自愈修正后的选择器
    if locator == "#login-btn-missing":
        raise TimeoutError(f"等待元素 {locator} 超时")
    assert locator == "#login-btn"


def test_tc_003():
    import pytest
    pytest.skip("暂不支持")
'''

# LLM 返回的违规修复：试图给失败断言加 or True 放水（必须被拒）
TESTS_ASSERT_HACK = '''"""自愈样例脚本：1 过 / 1 选择器失效（超时异常）/ 1 skip。"""


def test_tc_001():
    assert 1 + 1 == 2


def test_tc_002():
    locator = "#login-btn"
    if locator == "#login-btn-missing":
        raise TimeoutError(f"等待元素 {locator} 超时")
    assert locator == "#login-btn" or True  # 违规：修改断言


def test_tc_003():
    import pytest
    pytest.skip("暂不支持")
'''

CASES = json.dumps([
    {"case_id": "TC-001", "title": "登录成功 -> 跳转首页"},
    {"case_id": "TC-002", "title": "输入账密 -> 点击登录"},
    {"case_id": "TC-003", "title": "锁定账户 -> 提示锁定"},
], ensure_ascii=False)

MAPPING = {"test_tc_001": "TC-001", "test_tc_002": "TC-002", "test_tc_003": "TC-003"}


class FakeLLM:
    """固定返回诊断 JSON 的假客户端（协议同 LangChainClient.chat）。"""

    def __init__(self, diag: dict):
        self.diag = diag
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs) -> str:
        self.prompts.append(messages[-1]["content"])
        return json.dumps(self.diag, ensure_ascii=False)


def _diag_selector() -> dict:
    return {
        "root_cause": "selector",
        "analysis": "选择器 #login-btn-missing 已失效，页面改版后登录按钮为 #login-btn",
        "fixed_file": "test_cases_0.py",
        "fixed_content": TESTS_HEAL_FIXED,
    }


def _diag_assert_hack() -> dict:
    return {
        "root_cause": "selector",
        "analysis": "选择器失效",
        "fixed_file": "test_cases_0.py",
        "fixed_content": TESTS_ASSERT_HACK,
    }


def _diag_product_bug() -> dict:
    return {
        "root_cause": "product_bug",
        "analysis": "页面行为符合需求描述但断言失败，疑似被测系统真缺陷",
        "fixed_file": "",
        "fixed_content": "",
    }


def _diag_useless_fix() -> dict:
    """看似修复了但实际没修（重跑仍失败）。"""
    return {
        "root_cause": "selector",
        "analysis": "换了个选择器",
        "fixed_file": "test_cases_0.py",
        "fixed_content": TESTS_HEAL,  # 与原文件相同，重跑照样超时失败
    }


def _make_heal_scripts(db, task, content: str = TESTS_HEAL):
    """任务目录下生成最小 auto/ 脚本集（无浏览器依赖）。"""
    auto = OUTPUT_DIR / task.user_data_dir(db) / task.id / "auto"
    auto.mkdir(parents=True, exist_ok=True)
    (auto / "conftest.py").write_text(CONFTEST_MIN, encoding="utf-8")
    (auto / "mapping.json").write_text(json.dumps(MAPPING), encoding="utf-8")
    (auto / "test_cases_0.py").write_text(content, encoding="utf-8")
    return auto


def _db_run(run_id: str) -> ExecutionRun:
    db = SessionLocal()
    try:
        return db.get(ExecutionRun, run_id)
    finally:
        db.close()


@pytest.fixture()
def heal_env(monkeypatch):
    """返回 (install, rerun_calls)：install(fake) 换掉自愈 LLM 客户端；重跑探针记录 node id。"""
    rerun_calls: list[list[str]] = []
    import app.services.auto_healer as ah

    real_rerun = ah._rerun_failed

    def _spy(task_dir, env, node_ids, report_rel):
        rerun_calls.append(list(node_ids))
        return real_rerun(task_dir, env, node_ids, report_rel)

    monkeypatch.setattr(ah, "_rerun_failed", _spy)

    def _install(fake: FakeLLM):
        import app.services.auto_runner as ar
        monkeypatch.setattr(ar, "_resolve_heal_client", lambda: fake)

    return _install, rerun_calls


def test_heal_selector_fix_full_flow(client, accounts, db_session, heal_env):
    """selector 类：修复文件落盘 + 备份存在 + 只重跑失败 node id + heal 进 report_json。"""
    install, rerun_calls = heal_env
    install(FakeLLM(_diag_selector()))

    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    auto = _make_heal_scripts(db_session, task)

    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    detail = _wait_finished(client, token, run_id)

    assert detail["status"] == "completed", detail
    assert detail["heal_round"] == 1
    report = detail["report"]
    assert report["heal"]["rounds"] == 1
    assert report["heal"]["suspected_bugs"] == []
    entry = report["heal"]["log"][0]
    assert entry["round"] == 1
    assert entry["diagnosis"]["root_cause"] == "selector"
    assert entry["changed_files"] == ["test_cases_0.py"]
    assert entry["rerun_outcome"] == "passed"
    assert entry["suspects"][0]["case_id"] == "TC-002"

    # 修复后的用例 outcome 更新为 passed，重算 summary：3 条 2 过 0 败 1 跳
    by_id = {c["case_id"]: c for c in report["cases"]}
    assert by_id["TC-002"]["outcome"] == "passed"
    assert (report["summary"]["passed"], report["summary"]["failed"]) == (2, 0)

    # 文件真实落盘（含修复后的选择器）+ 备份存在（含旧选择器）
    fixed_src = (auto / "test_cases_0.py").read_text(encoding="utf-8")
    assert '"#login-btn"' in fixed_src and "自愈修正后的选择器" in fixed_src
    backup = auto / "heal_backup" / "round1" / "test_cases_0.py"
    assert backup.is_file(), "旧文件必须备份到 heal_backup/round1/"
    assert "#login-btn-missing" in backup.read_text(encoding="utf-8")

    # 只重跑了失败用例的 node id
    assert rerun_calls == [["auto/test_cases_0.py::test_tc_002"]]

    # DB 落库核对：heal_round / heal_log
    run = _db_run(run_id)
    assert run.heal_round == 1
    log = json.loads(run.heal_log)
    assert log[0]["rerun_outcome"] == "passed"


def test_heal_product_bug_no_change(client, accounts, db_session, heal_env):
    """product_bug：不改任何文件、suspected_bugs 进 report_json、一轮即停止。"""
    install, rerun_calls = heal_env
    install(FakeLLM(_diag_product_bug()))

    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    auto = _make_heal_scripts(db_session, task)
    before = (auto / "test_cases_0.py").read_text(encoding="utf-8")

    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = _wait_finished(client, token, r.json()["run_id"])

    assert detail["status"] == "completed"
    assert detail["heal_round"] == 1
    report = detail["report"]
    heal_meta = report["heal"]
    assert heal_meta["rounds"] == 1
    assert heal_meta["log"][0]["diagnosis"]["root_cause"] == "product_bug"
    assert heal_meta["log"][0]["changed_files"] == []
    assert heal_meta["log"][0]["rerun_outcome"] == "skipped"

    # suspected_bugs 进报告，失败用例如实保留 failed
    bugs = heal_meta["suspected_bugs"]
    assert len(bugs) == 1 and bugs[0]["case_id"] == "TC-002" and bugs[0]["kind"] == "product_bug"
    by_id = {c["case_id"]: c for c in report["cases"]}
    assert by_id["TC-002"]["outcome"] == "failed"

    # 不改任何文件、不备份、不重跑
    assert (auto / "test_cases_0.py").read_text(encoding="utf-8") == before
    assert not (auto / "heal_backup").exists()
    assert rerun_calls == []


def test_heal_rejects_assert_modification(client, accounts, db_session, heal_env, monkeypatch):
    """断言保护：LLM 返回修改断言的修复 → 拒绝写回，文件原样，轮次耗尽收敛。"""
    monkeypatch.setattr(config, "AUTO_HEAL_ROUNDS", 2)
    install, rerun_calls = heal_env
    install(FakeLLM(_diag_assert_hack()))

    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    auto = _make_heal_scripts(db_session, task)
    before = (auto / "test_cases_0.py").read_text(encoding="utf-8")

    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = _wait_finished(client, token, r.json()["run_id"])

    assert detail["status"] == "completed"
    assert detail["heal_round"] == 2  # 跑满 2 轮（本测试临时上限）
    report = detail["report"]
    assert report["heal"]["rounds"] == 2
    assert all(e["rerun_outcome"] == "rejected_assert_change" for e in report["heal"]["log"])
    # 违规修复绝不写回，也无备份、无重跑
    assert (auto / "test_cases_0.py").read_text(encoding="utf-8") == before
    assert not (auto / "heal_backup").exists()
    assert rerun_calls == []


def test_heal_rounds_exhausted(client, accounts, db_session, heal_env, monkeypatch):
    """轮次耗尽：修复无效 → 跑满上限轮后收敛，suspected_bugs 兜底记录。"""
    monkeypatch.setattr(config, "AUTO_HEAL_ROUNDS", 2)
    install, rerun_calls = heal_env
    install(FakeLLM(_diag_useless_fix()))

    token = accounts["user"]["token"]
    task = _insert_task(db_session)
    auto = _make_heal_scripts(db_session, task)

    r = client.post(f"/api/tasks/{task.id}/run-auto", headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = _wait_finished(client, token, r.json()["run_id"])

    assert detail["status"] == "completed"
    assert detail["heal_round"] == 2
    report = detail["report"]
    heal_meta = report["heal"]
    assert heal_meta["rounds"] == 2
    assert [e["rerun_outcome"] for e in heal_meta["log"]] == ["failed", "failed"]
    # 每轮都只重跑失败 node id
    assert rerun_calls == [["auto/test_cases_0.py::test_tc_002"]] * 2
    # 用例如实保留 failed；轮次耗尽 → suspected_bugs 兜底
    by_id = {c["case_id"]: c for c in report["cases"]}
    assert by_id["TC-002"]["outcome"] == "failed"
    bugs = heal_meta["suspected_bugs"]
    assert bugs and bugs[0]["case_id"] == "TC-002" and bugs[0]["kind"] == "exhausted"
    # 备份每轮都有（修复前快照）
    assert (auto / "heal_backup" / "round1" / "test_cases_0.py").is_file()
    assert (auto / "heal_backup" / "round2" / "test_cases_0.py").is_file()


def test_assertion_guard_unit():
    """断言保护校验函数单测：删断言/改期望值/改消息均被拒，重排格式与新增断言放行。"""
    from app.services.auto_healer import _assertions_preserved

    old = 'def f():\n    assert a == 1\n    assert b == 2, "消息"\n'
    # 原样 → 放行
    assert _assertions_preserved(old, old)
    # 删断言 → 拒
    assert not _assertions_preserved(old, 'def f():\n    assert a == 1\n')
    # 改期望值 → 拒
    assert not _assertions_preserved(
        old, 'def f():\n    assert a == 2\n    assert b == 2, "消息"\n')
    # 改断言消息 → 拒
    assert not _assertions_preserved(
        old, 'def f():\n    assert a == 1\n    assert b == 2, "改了"\n')
    # 仅重排空白 → 放行
    assert _assertions_preserved(
        old, 'def f():\n    assert   a == 1\n\n    assert b == 2, "消息"\n')
    # 新增断言 → 放行（加强校验不算放松）
    assert _assertions_preserved(old, old + '    assert c == 3\n')
