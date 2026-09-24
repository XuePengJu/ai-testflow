"""M5 探索式测试 Agent 单测：ReAct 全链路（fake LLM + fake 浏览器）、护栏、预算收敛、
任务链路接入（engine explore 分支 + create_task kind=explore）、真实 Playwright file:// 冒烟。"""
import json
import uuid
from pathlib import Path

import pytest

from app.services import explorer_agent
from app.services.explorer_agent import (
    ExploreOutcome,
    is_dangerous,
    parse_decision,
    run_explore,
)

# ---- 测试夹具：fake 浏览器（内存页面图）与 fake LLM（脚本化决策序列） ----

ENTRY = "https://demo.test/"
FORM = "https://demo.test/goods/new"


class FakePage:
    """内存中的一页：元素列表 + 链接跳转关系。"""

    def __init__(self, url: str, title: str, elements: list[dict], links: dict | None = None):
        self.url = url
        self.title = title
        self.elements = elements          # [{"ref","role","name"}]
        self.links = links or {}          # 元素 name → 目标 url


class FakeBrowser:
    """与 BrowserSession 同方法协议的假浏览器（不发真实请求、不起进程）。"""

    def __init__(self, pages: dict[str, FakePage], entry: str):
        self.pages = pages
        self.current = entry
        self.registry: dict[str, dict] = {}
        self.storage_saved = ""

    @property
    def url(self) -> str:
        return self.current

    @property
    def title(self) -> str:
        return self.pages[self.current].title

    def goto(self, url: str) -> tuple[bool, str]:
        if url not in self.pages:
            return False, f"打开失败：未知页面 {url}"
        self.current = url
        return True, "ok"

    def back(self) -> tuple[bool, str]:
        return True, "ok"

    def snapshot(self) -> tuple[str, dict[str, dict]]:
        page = self.pages[self.current]
        lines = [f"URL：{page.url}", f"标题：{page.title}"]
        self.registry = {}
        for i, el in enumerate(page.elements, start=1):
            ref = el["ref"]
            self.registry[ref] = {"role": el["role"], "name": el["name"],
                                  "css": f"#fake-{ref}"}
            lines.append(f"[{ref}] {el['role']} {el['name']}")
        return "\n".join(lines), dict(self.registry)

    def click(self, ref: str) -> tuple[bool, str]:
        info = self.registry.get(ref)
        if info is None:
            return False, f"ref {ref} 无效"
        target = self.pages[self.current].links.get(info["name"])
        if target:
            self.current = target
        return True, "ok"

    def fill(self, fields: list[dict]) -> tuple[bool, str]:
        for f in fields:
            if str(f.get("ref")) not in self.registry:
                return False, f"ref {f.get('ref')} 无效"
        return True, "ok"

    def screenshot(self, path: str) -> tuple[bool, str]:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG-fake")
        return True, "ok"

    def try_login(self, credentials: dict) -> str:
        # 当前页有「密码」输入框则视为登录成功（模拟 web_crawler 的启发式判定）
        has_pwd = any("密码" in el["name"] for el in self.pages[self.current].elements)
        return "success" if has_pwd else "none"

    def save_storage_state(self, path: str) -> str:
        self.storage_saved = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")
        return path

    def close(self) -> None:
        pass


class FakeLLM:
    """按脚本依次吐出决策 JSON 的假 LLM（记录每次调用）。"""

    def __init__(self, decisions: list[dict]):
        self.decisions = list(decisions)
        self.calls: list[list] = []

    def chat(self, messages: list, **kwargs) -> str:
        self.calls.append(messages)
        d = self.decisions.pop(0)
        return json.dumps(d, ensure_ascii=False)


def _demo_pages() -> dict[str, FakePage]:
    """两页小站：首页（链接 + 危险按钮场景由单测按需增删）→ 商品表单页。"""
    return {
        ENTRY: FakePage(ENTRY, "首页", [
            {"ref": "e1", "role": "link", "name": "商品管理"},
            {"ref": "e2", "role": "button", "name": "新增商品"},
        ], links={"商品管理": FORM}),
        FORM: FakePage(FORM, "商品表单", [
            {"ref": "e1", "role": "textbox", "name": "商品名称"},
            {"ref": "e2", "role": "button", "name": "保存"},
        ]),
    }


_GOOD_CASE = {
    "title": "新增商品",
    "module": "商品管理",
    "case_type": "正向",
    "priority": "P1",
    "pre_condition": "已登录",
    "steps": ["打开商品表单", "填写商品名称", "点击保存"],
    "step_expectations": ["表单展示", "名称回显", "保存成功"],
    "expected": "商品保存成功",
    "test_data": "测试商品A",
}


# ---- 1. 决策解析与用例归一化（纯函数） ----

def test_parse_decision_accepts_fenced_json():
    raw = "```json\n{\"reason\": \"看页面\", \"tool\": \"browser_snapshot\", \"args\": {}}\n```"
    plan = parse_decision(raw)
    assert plan["tool"] == "browser_snapshot"


def test_parse_decision_rejects_garbage():
    with pytest.raises(ValueError):
        parse_decision("我想点击登录按钮")   # 无 JSON
    with pytest.raises(ValueError):
        parse_decision('{"reason": "缺 tool"}')  # 缺 tool 字段


def test_normalize_case_fallback_and_drop():
    from app.services.explorer_agent import _normalize_case
    c = _normalize_case({"title": "非法类型用例", "case_type": "不知道", "priority": "P9",
                         "steps": ["步骤一", ""]})
    assert c["case_type"] == "正向" and c["priority"] == "P1"
    assert c["step_expectations"] == ["步骤一预期兜底"] or c["step_expectations"]  # 逐步预期有兜底
    assert _normalize_case({"title": ""}) is None       # 缺标题丢弃
    assert _normalize_case("not-a-dict") is None


def test_is_dangerous_keywords():
    assert is_dangerous("删除商品") == "删除"
    assert is_dangerous("确认支付") == "支付"
    assert is_dangerous("Logout") == "logout"
    assert is_dangerous("保存") == ""


# ---- 2. ReAct 全链路（fake LLM + fake 浏览器） ----

def test_full_react_loop_produces_cases(tmp_path):
    """导航 → 快照 → 点击 → 填表 → submit_cases(done) 全链路：用例落袋、步骤留痕、截图落盘。"""
    llm = FakeLLM([
        {"reason": "先看首页", "tool": "browser_snapshot", "args": {}},
        {"reason": "进入商品表单", "tool": "browser_click", "args": {"ref": "e1"}},
        {"reason": "填商品名", "tool": "browser_fill",
         "args": {"fields": [{"ref": "e1", "value": "测试商品A"}]}},
        {"reason": "功能流探索充分", "tool": "submit_cases",
         "args": {"cases": [_GOOD_CASE], "done": True}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          goal="探索商品管理", browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))

    assert outcome.stop_reason == "done"
    assert len(outcome.cases) == 1
    case = outcome.cases[0]
    assert case["title"] == "新增商品" and case["case_type"] == "正向"
    assert case["step_expectations"] == ["表单展示", "名称回显", "保存成功"]
    # 步骤留痕：入口打开 + 4 个 LLM 决策步
    assert len(outcome.steps) == 5
    assert [s["action"] for s in outcome.steps] == [
        "browser_navigate", "browser_snapshot", "browser_click", "browser_fill", "submit_cases"]
    # 每步含 URL / 动作 / LLM 理由 / 截图相对路径，截图文件真实落盘
    for s in outcome.steps:
        assert s["url"] and s["action"] and s["reason"]
        assert s["screenshot"].startswith("explore/step-")
        assert (tmp_path / s["screenshot"]).is_file()
    # details 可 JSON 序列化（StepLog.input_summary 契约）
    payload = json.dumps(outcome.details, ensure_ascii=False)
    assert '"stop_reason": "done"' in payload
    # 点击后页面确实切换到了表单页（page_obs 记录了两个页面）
    assert FORM in outcome.page_obs


def test_llm_unavailable_converges_without_browser(tmp_path):
    """未配置模型：不启动浏览器直接优雅收敛（后台任务安全，不访问网络）。"""
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=None)
    assert outcome.stop_reason == "llm_unavailable"
    assert outcome.cases == [] and outcome.steps == []


# ---- 3. 护栏：域名锁定 / 危险操作黑名单 ----

def test_domain_lock_rejects_and_feeds_back(tmp_path):
    """LLM 请求越域 URL → 拒绝执行，拒绝原因进入 history 反馈，循环继续。"""
    llm = FakeLLM([
        {"reason": "想去外域", "tool": "browser_navigate", "args": {"url": "https://evil.com/x"}},
        {"reason": "继续域内", "tool": "browser_snapshot", "args": {}},
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [_GOOD_CASE], "done": True}},
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)

    assert outcome.stop_reason == "done"
    # 越域步被标记拒绝，且浏览器从未离开 demo.test
    nav = outcome.steps[1]
    assert nav["action"] == "browser_navigate" and "拒绝" in nav["result"] and "越域" in nav["result"]
    assert browser.current == ENTRY
    # 拒绝原因回喂 LLM（决策上下文的 history 里可见）
    assert any("越域" in json.dumps(m, ensure_ascii=False) for m in llm.calls[-1])
    # 后续用例照常提交
    assert len(outcome.cases) == 1


def test_dangerous_blacklist_blocks_click(tmp_path):
    """点击「删除」按钮 → 命中黑名单直接拒绝并反馈。"""
    pages = _demo_pages()
    pages[ENTRY].elements.append({"ref": "e3", "role": "button", "name": "删除商品"})
    llm = FakeLLM([
        {"reason": "试试删除", "tool": "browser_click", "args": {"ref": "e3"}},
        {"reason": "被拒后结束", "tool": "submit_cases", "args": {"cases": [], "done": True}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(pages, ENTRY))

    assert outcome.stop_reason == "done"
    click = outcome.steps[1]
    assert click["action"] == "browser_click" and "危险操作黑名单" in click["result"]
    assert any("黑名单" in json.dumps(m, ensure_ascii=False) for m in llm.calls[-1])


def test_invalid_ref_rejected(tmp_path):
    """点击快照中不存在的 ref → 拒绝并提示先快照。"""
    llm = FakeLLM([
        {"reason": "瞎点", "tool": "browser_click", "args": {"ref": "e99"}},
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [], "done": True}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    assert "无效" in outcome.steps[1]["result"]


# ---- 4. 预算控制：优雅收敛、已提交用例保留 ----

def test_max_steps_budget_keeps_submitted_cases(tmp_path, monkeypatch):
    """MAX_STEPS=2：第 1 步提交用例、第 2 步后收敛，已提交用例保留。"""
    monkeypatch.setenv("EXPLORE_MAX_STEPS", "2")
    llm = FakeLLM([
        {"reason": "先交一批", "tool": "submit_cases",
         "args": {"cases": [_GOOD_CASE], "done": False}},
        {"reason": "再看一眼", "tool": "browser_snapshot", "args": {}},
        {"reason": "不应被执行", "tool": "browser_snapshot", "args": {}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))

    assert outcome.stop_reason == "max_steps"
    assert len(outcome.cases) == 1, "已提交用例必须保留（优雅收敛）"
    assert len(llm.calls) == 2, "第 3 个决策不应被消费"
    assert "max_steps" in outcome.summary


def test_token_budget_converges(tmp_path, monkeypatch):
    """token 预算耗尽 → 首轮决策后即收敛且不崩。"""
    monkeypatch.setenv("EXPLORE_MAX_TOKENS", "10")
    llm = FakeLLM([
        {"reason": "看页面", "tool": "browser_snapshot", "args": {}},
        {"reason": "不应被消费", "tool": "browser_snapshot", "args": {}},
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    assert outcome.stop_reason == "token_budget"
    assert len(llm.calls) == 1, "首轮决策消耗预算后，第二轮之前即收敛"


def test_login_with_credentials(tmp_path):
    """有账密 → 首步自动登录，storage_state 落盘，登录态记录进 details。"""
    pages = _demo_pages()
    pages[ENTRY].elements.append({"ref": "e9", "role": "textbox", "name": "密码"})
    llm = FakeLLM([
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [], "done": True}},
    ])
    outcome = run_explore(ENTRY, {"username": "admin", "password": "secret"},
                          out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(pages, ENTRY))

    assert outcome.login == "success"
    assert outcome.steps[1]["action"] == "browser_login"
    assert outcome.details["storage_state"].endswith("storage_state.json")
    assert (tmp_path / "explore" / "storage_state.json").is_file()


# ---- 5. 任务链路：create_task kind=explore + engine explore 分支 ----

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


def test_create_task_explore_auto_conversation(client, accounts, db_session):
    """kind=explore 未传会话：自动建 target + 会话 + 两条消息（复用 e2e 兜底逻辑）。"""
    token = accounts["user"]["token"]
    r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
        "kind": "explore", "url": "https://example.com", "name": "探索冒烟",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "explore" and body["target_id"]
    conv_id = body["conversation_id"]
    assert conv_id, "explore 任务必须享受 e2e 同样的会话兜底"

    from app.models.conversation import Conversation, Message
    conv = db_session.get(Conversation, conv_id)
    assert conv is not None and conv.title == "探索冒烟"
    msgs = (db_session.query(Message)
            .filter(Message.conversation_id == conv_id)
            .order_by(Message.id.asc()).all())
    assert [m.content for m in msgs] == [
        "🤖 发起探索式测试：https://example.com",
        "正在自主探索被测系统并生成测试用例…",
    ]


def test_create_task_explore_requires_url_or_target(client, accounts):
    """kind=explore：url / target_id 都不传 → 400。"""
    token = accounts["user"]["token"]
    r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
        "kind": "explore", "name": "缺地址",
    })
    assert r.status_code == 400


def test_engine_explore_wiring(client, accounts, db_session, monkeypatch):
    """engine kind=explore 分支接线：explore → scripter → exporter 三步，任务 completed。"""
    from app.models.task import StepLog, Task
    from app.workflow import engine

    task = Task(id=f"explore-{uuid.uuid4().hex[:6]}", name="引擎接线", kind="explore",
                source_type="url", input_ref=ENTRY, formats="xlsx,json",
                status="pending", user_id=_user_id("alice"))
    db_session.add(task)
    db_session.commit()

    def fake_run(url, credentials=None, out_dir=None, llm_client=None,
                 goal="", progress_cb=None, browser_factory=None):
        return ExploreOutcome(
            cases=[dict(_GOOD_CASE)],
            steps=[{"n": 1, "url": url, "action": "browser_navigate", "args": {},
                    "reason": "打开入口页", "result": "ok", "screenshot": "explore/step-001.png"}],
            summary="探索 1 步，提交 1 条用例（登录：none，收敛：done）",
            stop_reason="done", login="none",
            details={"url": url, "login": "none", "stop_reason": "done", "steps": [],
                     "cases_submitted": 1},
            page_obs={url: "URL：" + url},
        )

    monkeypatch.setattr(engine.explorer_agent, "run_explore", fake_run)
    # scripter 自解析 LLM（resolve_effective → mock → None）会拒绝生成；注入假脚本客户端
    from app.workflow.agents import scripter_agent as _scripter

    class FakeScriptLLM:
        def generate(self, prompt: str) -> str:
            return 'def test_tc_001():\n    """fake 脚本"""\n    assert True\n'

    monkeypatch.setattr(_scripter, "_resolve_llm_client",
                        lambda: (FakeScriptLLM(), "fake-model"))
    engine.run_task(task.id)

    db_session.expire_all()
    t = db_session.get(Task, task.id)
    assert t.status == "completed", f"explore 任务应收敛完成：{t.status}"
    assert t.cases_count == 1
    names = [s.name for s in (db_session.query(StepLog)
                              .filter(StepLog.task_id == task.id).order_by(StepLog.id).all())]
    assert names == ["explore", "scripter", "exporter"]
    assert '"explore"' in (t.report_json or ""), "report_json 应含探索摘要"
    assert "TC-001" in (t.cases_json or ""), "用例 ID 已补全"
    explore_step = db_session.query(StepLog).filter(
        StepLog.task_id == task.id, StepLog.name == "explore").first()
    assert explore_step.input_summary and "stop_reason" in explore_step.input_summary
    scripter_step = db_session.query(StepLog).filter(
        StepLog.task_id == task.id, StepLog.name == "scripter").first()
    assert "1 个脚本文件" in (scripter_step.output_summary or "")


# ---- 6. 真实 Playwright file:// 冒烟（浏览器不可用时跳过，不阻塞 CI） ----

def test_real_playwright_file_page(tmp_path):
    """真实 Chromium 打开本地 file:// 页面：快照/截图/填表真实可转。"""
    page1 = tmp_path / "index.html"
    page1.write_text(
        "<html><head><title>探索页</title></head><body>"
        '<button id="save-btn">保存订单</button>'
        '<a id="next" href="page2.html">下一页</a></body></html>', encoding="utf-8")
    page2 = tmp_path / "page2.html"
    page2.write_text(
        "<html><head><title>表单页</title></head><body>"
        '<input placeholder="用户名"><button>提交信息</button></body></html>', encoding="utf-8")

    llm = FakeLLM([
        {"reason": "看首页", "tool": "browser_snapshot", "args": {}},
        {"reason": "去第二页", "tool": "browser_navigate", "args": {"url": page2.as_uri()}},
        {"reason": "填用户名", "tool": "browser_fill",
         "args": {"fields": [{"ref": "e1", "value": "张三"}]}},
        {"reason": "结束", "tool": "submit_cases", "args": {"cases": [_GOOD_CASE], "done": True}},
    ])
    out_dir = tmp_path / "out"
    outcome = run_explore(page1.as_uri(), out_dir=str(out_dir), llm_client=llm)

    if outcome.stop_reason == "error" and "浏览器不可用" in outcome.summary:
        pytest.skip("Playwright/Chromium 不可用，跳过真实浏览器冒烟")

    assert outcome.stop_reason == "done", outcome.summary
    assert outcome.stop_reason == "done"
    # 首页快照真实取到了按钮与链接
    first_obs = next(obs for url, obs in outcome.page_obs.items() if url.endswith("index.html"))
    assert "保存订单" in first_obs and "下一页" in first_obs
    # 第二页快照取到了表单控件，填写真实生效
    second_obs = next(obs for url, obs in outcome.page_obs.items() if url.endswith("page2.html"))
    assert "用户名" in second_obs
    assert len(outcome.cases) == 1
    shots = list((out_dir / "explore").glob("step-*.png"))
    assert len(shots) >= 4, "每个动作步都有真实截图落盘"
