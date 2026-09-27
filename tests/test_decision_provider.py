"""分层决策中间层单测：FastDecider 解析 / 升级路由 / 分层 ReAct 循环集成 / 震荡拦截。"""
import json
from pathlib import Path

import pytest

from app.services.decision_provider import (
    CONFIDENCE_HIGH,
    FAST_ACTIONS,
    FastDecider,
    _parse_fast,
)
from app.services.explorer_agent import run_explore

ENTRY = "https://demo.test/"
FORM = "https://demo.test/goods/new"


# ---- 夹具：与 test_explorer_agent 同协议的 fake 浏览器 / LLM ----

class FakePage:
    def __init__(self, url, title, elements, links=None):
        self.url, self.title, self.elements, self.links = url, title, elements, links or {}


class FakeBrowser:
    def __init__(self, pages, entry):
        self.pages, self.current = pages, entry
        self.registry = {}

    @property
    def url(self):
        return self.current

    @property
    def title(self):
        return self.pages[self.current].title

    def goto(self, url):
        if url not in self.pages:
            return False, f"打开失败：未知页面 {url}"
        self.current = url
        return True, "ok"

    def back(self):
        return True, "ok"

    def snapshot(self):
        page = self.pages[self.current]
        lines = [f"URL：{page.url}", f"标题：{page.title}"]
        self.registry = {}
        for i, el in enumerate(page.elements, start=1):
            ref = el["ref"]
            self.registry[ref] = {"role": el["role"], "name": el["name"], "css": f"#fake-{ref}"}
            lines.append(f"[{ref}] {el['role']} {el['name']}")
        return "\n".join(lines), dict(self.registry)

    def click(self, ref):
        info = self.registry.get(ref)
        if info is None:
            return False, "ref 无效"
        target = self.pages[self.current].links.get(info["name"])
        if target:
            self.current = target
        return True, "ok"

    def fill(self, fields):
        for f in fields:
            if str(f.get("ref")) not in self.registry:
                return False, "ref 无效"
        return True, "ok"

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG-fake")
        return True, "ok"

    def try_login(self, credentials):
        return "none"

    def save_storage_state(self, path):
        return ""

    def close(self):
        pass


class FakeDualLLM:
    """fast 判断与主 LLM 决策共用一个客户端实例，按 system prompt 特征分流。"""

    def __init__(self, fast_decisions, main_decisions):
        self.fast = list(fast_decisions)
        self.main = list(main_decisions)
        self.fast_calls = 0
        self.main_calls = 0

    def chat(self, messages, **kwargs):
        sys_text = str(messages[0]["content"]) if messages else ""
        if "浏览器探索决策器" in sys_text:
            self.fast_calls += 1
            d = self.fast.pop(0) if self.fast else {"action": "escalate"}
            return json.dumps(d, ensure_ascii=False)
        self.main_calls += 1
        return json.dumps(self.main.pop(0), ensure_ascii=False)


class FakePlainLLM:
    """不分层时用：所有调用都走主决策脚本。"""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return json.dumps(self.decisions.pop(0), ensure_ascii=False)


def _demo_pages():
    return {
        ENTRY: FakePage(ENTRY, "首页", [
            {"ref": "e1", "role": "link", "name": "商品管理"},
        ], links={"商品管理": FORM}),
        FORM: FakePage(FORM, "商品表单", [
            {"ref": "e1", "role": "textbox", "name": "商品名称"},
            {"ref": "e2", "role": "button", "name": "保存"},
        ]),
    }


_GOOD_CASE = {
    "title": "新增商品", "module": "商品管理", "case_type": "正向", "priority": "P1",
    "pre_condition": "已登录", "steps": ["打开商品表单", "填写商品名称", "点击保存"],
    "step_expectations": ["表单展示", "名称回显", "保存成功"],
    "expected": "商品保存成功", "test_data": "测试商品A",
}


# ---- 1. _parse_fast 纯函数 ----

def test_parse_fast_valid_click():
    d = _parse_fast('{"action": "click", "ref": "e3", "reason": "点登录", "confidence": 0.9}')
    assert d.action == "browser_click" and d.args == {"ref": "e3"}
    assert d.confidence == 0.9
    assert d.next == "main"                      # 缺省 next 落 main（保守路由）


def test_parse_fast_next_routing():
    d = _parse_fast('{"action": "back", "confidence": 0.9, "next": "fast"}')
    assert d.next == "fast"
    d2 = _parse_fast('{"action": "back", "confidence": 0.9, "next": "huh"}')
    assert d2.next == "main"                     # 非法 next 落 main


def test_parse_fast_navigate():
    d = _parse_fast('{"action": "navigate", "url": "https://demo.test/goods/new", "confidence": 0.8}')
    assert d.action == "browser_navigate" and "goods/new" in d.args["url"]


def test_parse_fast_garbage_becomes_escalate():
    d = _parse_fast("我觉得应该点登录按钮")
    assert d.action == "escalate"


def test_parse_fast_unknown_action_becomes_escalate():
    d = _parse_fast('{"action": "fill", "confidence": 0.99}')
    assert d.action == "escalate"          # fill 需要生成，不在 FAST_ACTIONS
    assert "fill" not in FAST_ACTIONS


def test_parse_fast_click_missing_ref_becomes_escalate():
    d = _parse_fast('{"action": "click", "confidence": 0.9}')
    assert d.action == "escalate"


def test_parse_fast_confidence_clamped():
    d = _parse_fast('{"action": "back", "confidence": 5}')
    assert d.confidence == 1.0
    d2 = _parse_fast('{"action": "back", "confidence": -1}')
    assert d2.confidence == 0.0


def test_confidence_threshold_smoke():
    assert 0 < CONFIDENCE_HIGH < 1


# ---- 2. 分层 ReAct 循环集成 ----

def test_layered_loop_fast_path_hits(tmp_path, monkeypatch):
    """路由门控：main 决策标记 next=fast 后，纯浏览步骤由快判接管零浪费。"""
    monkeypatch.setenv("EXPLORE_LAYERED", "1")
    fast = [
        {"action": "click", "ref": "e2", "reason": "点保存", "confidence": 0.9, "next": "main"},
        {"action": "escalate", "reason": "需写用例"},
    ]
    main = [
        {"reason": "进商品管理", "tool": "browser_click", "args": {"ref": "e1"}, "next": "fast"},
        {"reason": "填表", "tool": "browser_fill",
         "args": {"fields": [{"ref": "e1", "value": "测试商品A"}]}, "next": "fast"},
        {"reason": "提交用例", "tool": "submit_cases",
         "args": {"cases": [_GOOD_CASE], "done": True}},
    ]
    llm = FakeDualLLM(fast, main)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm, goal="探索商品管理",
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    assert outcome.stop_reason == "done"
    assert llm.fast_calls == 2 and llm.main_calls == 3
    m = outcome.details["metrics"]
    assert m["fast_hits"] == 1
    assert m["escalations"] == 1
    assert m["fast_skipped"] == 2        # 步1（首步固定 main）+ 步3（fill 后 next=main 回主路）
    assert len(outcome.cases) == 1


def test_layered_loop_low_confidence_escalates(tmp_path, monkeypatch):
    """低置信 → 不执行，升级主 LLM 决策，fast_hits 为 0。"""
    monkeypatch.setenv("EXPLORE_LAYERED", "1")
    fast = [
        {"action": "click", "ref": "e2", "reason": "不确定", "confidence": 0.3},
    ]
    main = [
        {"reason": "进商品管理", "tool": "browser_click", "args": {"ref": "e1"}, "next": "fast"},
        {"reason": "提交用例", "tool": "submit_cases",
         "args": {"cases": [_GOOD_CASE], "done": True}},
    ]
    llm = FakeDualLLM(fast, main)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm, goal="探索",
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    assert outcome.stop_reason == "done"
    assert llm.main_calls == 2
    m = outcome.details["metrics"]
    assert m["fast_hits"] == 0 and m["escalations"] == 1


# ---- 3. 震荡拦截（不分层也生效） ----

def test_oscillation_blocked_after_second_repeat(tmp_path):
    """复现阶段0基线的真实震荡：反复 navigate 到打不开的页面（404），
    同一指纹第 2 次警告、第 3 次硬拒，不再白烧步数。"""
    dead = "https://demo.test/goods/new/add"   # 不存在的页面 → goto 失败
    decisions = [
        {"reason": "进新增页", "tool": "browser_navigate", "args": {"url": dead}},
        {"reason": "再试新增页", "tool": "browser_navigate", "args": {"url": dead}},
        {"reason": "又试新增页", "tool": "browser_navigate", "args": {"url": dead}},
        {"reason": "提交", "tool": "submit_cases", "args": {"cases": [_GOOD_CASE], "done": True}},
    ]
    llm = FakePlainLLM(decisions)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm, goal="探索",
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    m = outcome.details["metrics"]
    assert m["repeat_blocked"] == 1
    blocked = [s for s in outcome.steps if "震荡拦截" in s.get("result", "")]
    assert len(blocked) == 1
    assert outcome.stop_reason == "done"


def test_oscillation_fingerprint_differs_by_args(tmp_path):
    """不同 args 的 navigate 不算同一指纹：两次不同 url 都放行。"""
    other = "https://demo.test/orders"
    pages = _demo_pages()
    pages[other] = FakePage(other, "订单页", [{"ref": "e1", "role": "button", "name": "刷新"}])
    decisions = [
        {"reason": "去表单页", "tool": "browser_navigate", "args": {"url": FORM}},
        {"reason": "去订单页", "tool": "browser_navigate", "args": {"url": other}},
        {"reason": "提交", "tool": "submit_cases", "args": {"cases": [_GOOD_CASE], "done": True}},
    ]
    llm = FakePlainLLM(decisions)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm, goal="探索",
                          browser_factory=lambda: FakeBrowser(pages, ENTRY))
    assert outcome.details["metrics"]["repeat_blocked"] == 0
