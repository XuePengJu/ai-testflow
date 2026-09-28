"""M3 计划先行单测：探索计划生成/落盘/读取、confirm 结构校验（合法/非法）、
计划注入 run_explore 决策上下文、引擎 step 粒度暂停与确认恢复接线、
EXPLORE_PLAN_FIRST=0 关闭走旧自由探索路径。"""
import json
import uuid
from pathlib import Path

import pytest

from app.services import explorer_agent
from app.services.explorer_agent import (
    generate_explore_plan,
    load_explore_plan,
    normalize_plan_flows,
    run_explore,
    save_explore_plan,
    validate_plan_payload,
)

ENTRY = "https://demo.test/"


# ---- 1. 计划生成与落盘 ----

class _PlanLLM:
    """计划生成的假 LLM：返回固定业务流 JSON。"""

    def __init__(self, payload: dict | Exception):
        self.payload = payload

    def chat(self, messages: list, **kwargs) -> str:
        if isinstance(self.payload, Exception):
            raise self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def _no_browser() -> object:
    raise RuntimeError("测试环境无浏览器")


def test_generate_explore_plan_writes_plan_json(tmp_path):
    """LLM 正常产出 3 条业务流 → plan.json 落盘（confirmed=False，结构完整）。"""
    llm = _PlanLLM({"flows": [
        {"name": "商品浏览", "steps": ["打开商品列表", "查看商品详情"]},
        {"name": "搜索", "steps": ["输入关键词", "执行搜索", "查看结果"]},
        {"name": "购物车", "steps": ["加入购物车", "查看购物车"]},
    ], "reason": "覆盖核心三流"})
    result = generate_explore_plan(
        ENTRY, goal="测试电商站", llm_client=llm, out_dir=str(tmp_path),
        task_id="t1", browser_factory=_no_browser)
    assert len(result["flows"]) == 3
    assert result["details"]["fallback"] is False

    plan = load_explore_plan(str(tmp_path))
    assert plan is not None
    assert plan["confirmed"] is False
    assert plan["task_id"] == "t1"
    assert plan["goal"] == "测试电商站"
    assert plan["entry_url"] == ENTRY
    assert [f["name"] for f in plan["flows"]] == ["商品浏览", "搜索", "购物车"]


def test_generate_explore_plan_fallback_on_llm_failure(tmp_path):
    """LLM 挂掉/输出非法 → 兜底单条自由探索流，仍落盘进确认流程（不阻塞任务）。"""
    llm = _PlanLLM(RuntimeError("网络超时"))
    result = generate_explore_plan(
        ENTRY, goal="测试电商站", llm_client=llm, out_dir=str(tmp_path),
        task_id="t2", browser_factory=_no_browser)
    assert result["details"]["fallback"] is True
    assert len(result["flows"]) == 1
    plan = load_explore_plan(str(tmp_path))
    assert plan is not None and plan["confirmed"] is False and len(plan["flows"]) == 1

    # 输出非法（无 JSON）同样走兜底
    llm2 = _PlanLLM({"flows": [{"name": "缺步骤", "steps": []}]})
    result2 = generate_explore_plan(
        ENTRY, goal="g", llm_client=llm2, out_dir=str(tmp_path / "b"),
        task_id="t3", browser_factory=_no_browser)
    assert result2["details"]["fallback"] is True


def test_generate_explore_plan_strips_fenced_json(tmp_path):
    """模型输出带 markdown 围栏也能解析。"""

    class Fenced(_PlanLLM):
        def chat(self, messages: list, **kwargs) -> str:
            return "```json\n" + json.dumps(self.payload, ensure_ascii=False) + "\n```"

    llm = Fenced({"flows": [{"name": "登录流", "steps": ["输入账密", "点击登录"]}]})
    result = generate_explore_plan(
        ENTRY, goal="g", llm_client=llm, out_dir=str(tmp_path),
        task_id="t4", browser_factory=_no_browser)
    assert result["flows"][0]["name"] == "登录流"


# ---- 2. confirm 结构校验 ----

def test_validate_plan_payload_accepts_valid():
    flows = validate_plan_payload({"flows": [
        {"name": "登录", "steps": ["打开登录页", "输入账密", "提交"]},
        {"name": "", "steps": ["会被丢弃"]},
        {"name": "购物车", "steps": [123, "加入购物车", "  "]},
    ]})
    assert [f["name"] for f in flows] == ["登录", "购物车"]
    # 非字符串步骤会被 str() 归一化，空白步丢弃
    assert flows[1]["steps"] == ["123", "加入购物车"]


@pytest.mark.parametrize("payload", [
    "not-a-dict",
    {},
    {"flows": []},
    {"flows": "abc"},
    {"flows": [{"steps": ["缺名字"]}]},
    {"flows": [{"name": "缺步骤", "steps": []}]},
    {"flows": ["not-a-dict", 42]},
])
def test_validate_plan_payload_rejects_invalid(payload):
    with pytest.raises(ValueError):
        validate_plan_payload(payload)


def test_normalize_plan_flows_bounds():
    flows = normalize_plan_flows([
        {"name": "x" * 100, "steps": [f"s{i}" for i in range(20)]},
        "junk",
        {"name": "ok", "steps": ["a"]},
    ])
    assert len(flows) == 2
    assert len(flows[0]["name"]) == 60
    assert len(flows[0]["steps"]) == 10


def test_plan_roundtrip_and_missing(tmp_path):
    """save → load 往返一致；文件缺失 / 非 JSON → None。"""
    assert load_explore_plan(str(tmp_path)) is None
    assert load_explore_plan(None) is None
    save_explore_plan(str(tmp_path), {"confirmed": False, "flows": [{"name": "a", "steps": ["s"]}]})
    plan = load_explore_plan(str(tmp_path))
    assert plan is not None and plan["flows"][0]["name"] == "a"
    (tmp_path / "plan.json").write_text("{{broken", encoding="utf-8")
    assert load_explore_plan(str(tmp_path)) is None


# ---- 3. 计划注入 run_explore 决策上下文 ----

class _StepLLM:
    """决策假 LLM：记录调用并按脚本输出。"""

    def __init__(self, decisions: list[dict]):
        self.decisions = list(decisions)
        self.calls: list[list] = []

    def chat(self, messages: list, **kwargs) -> str:
        self.calls.append(messages)
        return json.dumps(self.decisions.pop(0), ensure_ascii=False)


class _StepBrowser:
    def __init__(self) -> None:
        self.current = ENTRY
        self.registry: dict[str, dict] = {}

    @property
    def url(self) -> str:
        return self.current

    @property
    def title(self) -> str:
        return "首页"

    def goto(self, url: str) -> tuple[bool, str]:
        self.current = url
        return True, "ok"

    def back(self) -> tuple[bool, str]:
        return True, "ok"

    def snapshot(self) -> tuple[str, dict[str, dict]]:
        self.registry = {"e1": {"role": "link", "name": "商品", "css": "#a",
                                "active": False, "href": ""}}
        return "URL：{}\n[e1] link 商品".format(self.current), dict(self.registry)

    def click(self, ref: str) -> tuple[bool, str]:
        return True, "ok"

    def fill(self, fields: list[dict]) -> tuple[bool, str]:
        return True, "ok"

    def screenshot(self, path: str) -> tuple[bool, str]:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")
        return True, "ok"

    def try_login(self, credentials: dict) -> str:
        return "none"

    def save_storage_state(self, path: str) -> str:
        return ""

    def close(self) -> None:
        pass


def test_run_explore_injects_confirmed_plan_context(tmp_path):
    """传入已确认计划 → 决策 prompt 出现计划文本，details 记录 plan_flows。"""
    llm = _StepLLM([
        {"reason": "按计划先看商品", "tool": "browser_snapshot", "args": {}},
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [], "done": True}},
    ])
    plan = {"confirmed": True, "flows": [
        {"name": "商品浏览", "steps": ["打开商品列表", "查看详情"]},
        {"name": "搜索", "steps": ["输入关键词", "搜索"]},
    ]}
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=_StepBrowser, plan=plan)
    assert outcome.stop_reason == "done"
    first_call = json.dumps(llm.calls[0], ensure_ascii=False)
    assert "用户已确认的探索计划" in first_call
    assert "业务流1：商品浏览" in first_call
    assert "2. 查看详情" in first_call
    assert outcome.details["plan_flows"] == ["商品浏览", "搜索"]


def test_run_explore_without_plan_keeps_old_behavior(tmp_path):
    """不传计划 → 决策 prompt 无计划文本，details.plan_flows 为空数组。"""
    llm = _StepLLM([
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [], "done": True}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=_StepBrowser)
    assert "探索计划" not in json.dumps(llm.calls[0], ensure_ascii=False)
    assert outcome.details["plan_flows"] == []


# ---- 4. 引擎接线：plan 步骤生成 → awaiting_confirm 暂停 → confirm 恢复 ----

def _user_id(username: str) -> int:
    from app.core.db import SessionLocal
    from app.models.user import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u, f"用户 {username} 不存在"
        return u.id
    finally:
        db.close()


def _make_explore_task(db_session) -> "object":
    from app.models.task import Task
    task = Task(id=f"plan-{uuid.uuid4().hex[:6]}", name="计划先行冒烟", kind="explore",
                source_type="url", input_ref=ENTRY, formats="xlsx,json",
                status="pending", user_id=_user_id("alice"))
    db_session.add(task)
    db_session.commit()
    return task


class _FakeRunRecorder:
    """记录 run_explore 收到的 plan 参数，返回一条用例的假探索。"""

    def __init__(self):
        self.plans: list = []

    def __call__(self, url, credentials=None, out_dir=None, llm_client=None,
                 goal="", progress_cb=None, browser_factory=None, plan=None):
        from app.services.explorer_agent import ExploreOutcome
        self.plans.append(plan)
        return ExploreOutcome(
            cases=[{"title": "探索用例", "module": "探索用例", "case_type": "正向",
                    "priority": "P1", "pre_condition": "", "steps": ["步骤一"],
                    "step_expectations": ["步骤一预期"], "expected": "成功",
                    "test_data": None}],
            steps=[], summary="探索 0 步，提交 1 条用例（登录：none，收敛：done）",
            stop_reason="done", login="none",
            details={"url": url, "login": "none", "stop_reason": "done", "steps": [],
                     "cases_submitted": 1, "plan_flows": [f["name"] for f in (plan or {}).get("flows", [])]},
            page_obs={},
        )


@pytest.fixture()
def engine_env(monkeypatch):
    """引擎测试公共桩：种子快照跳过（不起真浏览器）+ scripter 假 LLM + enqueue 捕获。"""
    from app.workflow import engine
    from app.workflow.agents import scripter_agent as _scripter

    monkeypatch.setattr(engine.explorer_agent, "_plan_seed_snapshot",
                        lambda *a, **k: "")

    class FakeScriptLLM:
        def generate(self, prompt: str) -> str:
            return "def test_tc_001():\n    assert True\n"

    monkeypatch.setattr(_scripter, "_resolve_llm_client",
                        lambda: (FakeScriptLLM(), "fake-model"))

    enqueued: list[str] = []
    monkeypatch.setattr("app.core.task_queue.enqueue", lambda tid: enqueued.append(tid))
    return {"enqueued": enqueued}


def test_engine_plan_first_pause_and_resume(client, accounts, db_session,
                                            engine_env, monkeypatch):
    """计划先行全链路：plan 步骤 → 暂停（awaiting_confirm）→ confirm → 恢复完成。"""
    from app.models.task import StepLog, Task
    from app.workflow import engine

    task = _make_explore_task(db_session)
    recorder = _FakeRunRecorder()
    monkeypatch.setattr(engine.explorer_agent, "run_explore", recorder)

    # 第一次运行：生成计划后 step 粒度暂停
    engine.run_task(task.id)
    db_session.expire_all()
    t = db_session.get(Task, task.id)
    assert t.status == "running", "暂停期间任务保持 running（不动全局状态枚举）"
    logs = (db_session.query(StepLog).filter(StepLog.task_id == task.id)
            .order_by(StepLog.id).all())
    assert [s.name for s in logs] == ["plan", "explore"]
    assert logs[0].status == "completed"
    assert logs[1].status == "awaiting_confirm"
    assert "确认" in (logs[1].progress or "")

    # plan.json 已落盘、未确认
    token = accounts["user"]["token"]
    r = client.get(f"/api/tasks/{task.id}/explore-plan",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exists"] is True and body["confirmed"] is False
    assert len(body["flows"]) >= 1

    # 确认（编辑后）→ 200，plan.json 覆盖为 confirmed=true，暂停步骤删除并重新入队
    edited = {"flows": [{"name": "编辑后的流", "steps": ["打开页面", "提交表单"]}]}
    r2 = client.post(f"/api/tasks/{task.id}/explore-plan/confirm",
                     headers={"Authorization": f"Bearer {token}"}, json=edited)
    assert r2.status_code == 200, r2.text
    assert r2.json()["ok"] is True
    assert engine_env["enqueued"] == [task.id], "确认后任务重新入队恢复执行"

    db_session.expire_all()
    plan = load_explore_plan(str(engine.OUTPUT_DIR / t.user_data_dir(db_session) / task.id))
    assert plan["confirmed"] is True
    assert plan["flows"][0]["name"] == "编辑后的流"

    # 恢复执行：plan 步骤跳过 → explore（消费已确认计划）→ scripter → exporter
    engine.run_task(task.id)
    db_session.expire_all()
    t = db_session.get(Task, task.id)
    assert t.status == "completed"
    logs = (db_session.query(StepLog).filter(StepLog.task_id == task.id)
            .order_by(StepLog.id).all())
    assert [s.name for s in logs] == ["plan", "explore", "scripter", "exporter"]
    assert logs[1].status == "completed"
    assert "编辑后的流" in (logs[1].input_summary or ""), "details 应记录消费的计划流"

    # run_explore 收到一次调用：恢复时收到已确认计划
    assert len(recorder.plans) == 1
    assert recorder.plans[0] is not None and recorder.plans[0]["confirmed"] is True

    # 已完成任务再确认 → 409
    r3 = client.post(f"/api/tasks/{task.id}/explore-plan/confirm",
                     headers={"Authorization": f"Bearer {token}"}, json=edited)
    assert r3.status_code == 409


def test_confirm_rejects_invalid_payload(client, accounts, db_session, engine_env,
                                         monkeypatch):
    """confirm 提交非法结构 → 400，计划不被覆盖。"""
    from app.models.task import StepLog
    from app.workflow import engine

    task = _make_explore_task(db_session)
    monkeypatch.setattr(engine.explorer_agent, "run_explore", _FakeRunRecorder())
    engine.run_task(task.id)  # 暂停

    token = accounts["user"]["token"]
    for bad in ({"flows": []}, {"flows": [{"name": "缺步骤", "steps": []}]}, {"oops": 1}):
        r = client.post(f"/api/tasks/{task.id}/explore-plan/confirm",
                        headers={"Authorization": f"Bearer {token}"}, json=bad)
        assert r.status_code == 400, f"{bad} 应 400：{r.text}"

    db_session.expire_all()
    plan = load_explore_plan(str(engine.OUTPUT_DIR / task.user_data_dir(db_session) / task.id))
    assert plan["confirmed"] is False, "非法提交不得覆盖确认状态"
    # 暂停步骤未被删除
    db_session.expire_all()
    awaiting = (db_session.query(StepLog)
                .filter(StepLog.task_id == task.id, StepLog.name == "explore").first())
    assert awaiting is not None and awaiting.status == "awaiting_confirm"


def test_confirm_without_plan_returns_404(client, accounts, db_session, engine_env):
    """计划尚未生成 → 404（任务先置为 running 以越过状态守卫）。"""
    task = _make_explore_task(db_session)
    task.status = "running"
    db_session.commit()
    token = accounts["user"]["token"]
    r = client.post(f"/api/tasks/{task.id}/explore-plan/confirm",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"flows": [{"name": "a", "steps": ["s"]}]})
    assert r.status_code == 404

    # 任务未运行（pending）时确认 → 409
    task2 = _make_explore_task(db_session)
    r2 = client.post(f"/api/tasks/{task2.id}/explore-plan/confirm",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"flows": [{"name": "a", "steps": ["s"]}]})
    assert r2.status_code == 409


def test_plan_first_disabled_keeps_legacy_path(client, accounts, db_session,
                                               engine_env, monkeypatch):
    """EXPLORE_PLAN_FIRST=0：无 plan 步骤、无暂停、不生成 plan.json、探索不消费计划。"""
    from app.models.task import StepLog, Task
    from app.workflow import engine

    monkeypatch.setenv("EXPLORE_PLAN_FIRST", "0")
    task = _make_explore_task(db_session)
    recorder = _FakeRunRecorder()
    monkeypatch.setattr(engine.explorer_agent, "run_explore", recorder)

    engine.run_task(task.id)
    db_session.expire_all()
    t = db_session.get(Task, task.id)
    assert t.status == "completed"
    logs = (db_session.query(StepLog).filter(StepLog.task_id == task.id)
            .order_by(StepLog.id).all())
    assert [s.name for s in logs] == ["explore", "scripter", "exporter"]
    assert load_explore_plan(str(engine.OUTPUT_DIR / t.user_data_dir(db_session) / task.id)) is None
    assert recorder.plans == [None], "旧路径不传 plan"
