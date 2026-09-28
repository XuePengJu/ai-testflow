"""M2 结构化护栏单测：URL 白名单构建（同域 / pages.json URL 集）、
跨域导航拦截与「越界被护栏拦截」提示、点击链接目标预检、
EXPLORE_GUARDRAILS=0 关闭后回落旧逻辑（域名锁 + 无点击预检）。
复用 test_explorer_agent 的 FakeBrowser/FakeLLM 模式（独立拷贝，避免测试间耦合）。"""
import json
from pathlib import Path

import pytest

from app.services.explorer_agent import build_url_whitelist, run_explore

ENTRY = "https://demo.test/"
FORM = "https://demo.test/goods/new"
EVIL = "https://evil.com/x"

_GOOD_CASE = {
    "title": "新增商品",
    "module": "商品管理",
    "case_type": "正向",
    "priority": "P1",
    "steps": ["打开商品表单", "填写商品名称"],
    "expected": "商品保存成功",
}


class FakePage:
    def __init__(self, url: str, title: str, elements: list[dict], links: dict | None = None):
        self.url = url
        self.title = title
        self.elements = elements          # [{"ref","role","name","href"?}]
        self.links = links or {}          # 元素 name → 点击后目标 url


class FakeBrowser:
    """与 BrowserSession 同方法协议的假浏览器；快照注册表携带 href（真实实现 M2 已带上）。"""

    def __init__(self, pages: dict[str, FakePage], entry: str):
        self.pages = pages
        self.current = entry
        self.registry: dict[str, dict] = {}

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
                                  "css": f"#fake-{ref}", "href": el.get("href", "")}
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
        return "none"

    def save_storage_state(self, path: str) -> str:
        return ""

    def close(self) -> None:
        pass


class FakeLLM:
    """按脚本依次吐出决策 JSON 的假 LLM（记录每次调用）。"""

    def __init__(self, decisions: list[dict]):
        self.decisions = list(decisions)
        self.calls: list[list] = []

    def chat(self, messages: list, **kwargs) -> str:
        self.calls.append(messages)
        return json.dumps(self.decisions.pop(0), ensure_ascii=False)


def _demo_pages() -> dict[str, FakePage]:
    return {
        ENTRY: FakePage(ENTRY, "首页", [
            {"ref": "e1", "role": "link", "name": "商品管理", "href": FORM},
            {"ref": "e2", "role": "link", "name": "外域链接", "href": EVIL},
        ], links={"商品管理": FORM, "外域链接": EVIL}),
        FORM: FakePage(FORM, "商品表单", [
            {"ref": "e1", "role": "textbox", "name": "商品名称"},
        ]),
        # 护栏关闭路径会真实跳到外域页，需要页面存在供快照
        EVIL: FakePage(EVIL, "外域页", [
            {"ref": "e1", "role": "button", "name": "外域按钮"},
        ]),
    }


def _end_decision() -> dict:
    return {"reason": "结束", "tool": "submit_cases",
            "args": {"cases": [_GOOD_CASE], "done": True}}


# ---- 1. 白名单构建 ----

def test_build_whitelist_same_domain_only():
    """explore 任务（无 pages.json）：同域白名单，url_keys=None（无 URL 集约束）。"""
    wl = build_url_whitelist(ENTRY)
    assert wl["hosts"] == {"demo.test"}
    assert wl["url_keys"] is None


def test_build_whitelist_from_pages_json():
    """crawler pages.json 存在（e2e）：取其 URL 集 + hosts 并集；兼容 dict 包裹与字符串数组。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pages.json"
        p.write_text(json.dumps([
            {"url": "https://demo.test/a", "title": "A"},
            {"url": "https://demo.test/b/", "title": "B"},
        ], ensure_ascii=False), encoding="utf-8")
        wl = build_url_whitelist(ENTRY, p)
        assert wl["hosts"] == {"demo.test"}
        assert ("https", "demo.test", "/a") in wl["url_keys"]
        # 末尾斜杠规范化
        assert ("https", "demo.test", "/b") in wl["url_keys"]

        p2 = Path(td) / "pages2.json"
        p2.write_text(json.dumps({"pages": ["https://demo.test/c"]}), encoding="utf-8")
        wl2 = build_url_whitelist(ENTRY, p2)
        assert ("https", "demo.test", "/c") in wl2["url_keys"]


def test_build_whitelist_pages_json_broken_falls_back():
    """pages.json 损坏 → 回落同域白名单（url_keys=None），不抛异常。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pages.json"
        p.write_text("not-json{{", encoding="utf-8")
        wl = build_url_whitelist(ENTRY, p)
        assert wl["hosts"] == {"demo.test"}
        assert wl["url_keys"] is None


# ---- 2. 护栏开启（默认）：导航拦截 / 放行 ----

def test_guardrails_blocks_cross_domain_with_guardrail_message(tmp_path):
    """跨域导航 → 拒绝且消息含「越界被护栏拦截」，浏览器未离开入口域。"""
    llm = FakeLLM([
        {"reason": "想去外域", "tool": "browser_navigate", "args": {"url": EVIL}},
        _end_decision(),
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)
    assert outcome.stop_reason == "done"
    nav = outcome.steps[1]
    assert nav["action"] == "browser_navigate"
    assert "越界被护栏拦截" in nav["result"]
    assert "拒绝" in nav["result"] and "越域" in nav["result"]
    assert browser.current == ENTRY
    # 拦截原因回喂 LLM
    assert any("越界被护栏拦截" in json.dumps(m, ensure_ascii=False) for m in llm.calls[-1])


def test_guardrails_allows_same_domain_navigation(tmp_path):
    """同域导航 → 正常放行执行。"""
    llm = FakeLLM([
        {"reason": "去表单页", "tool": "browser_navigate", "args": {"url": FORM}},
        _end_decision(),
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)
    assert outcome.stop_reason == "done"
    assert outcome.steps[1]["result"] == "ok"
    assert browser.current == FORM


def test_guardrails_pages_json_url_set_blocks_unlisted(tmp_path):
    """out_dir 有 crawler pages.json → 白名单收窄为 URL 集：同域但不在集合内的页面也拦截。"""
    (tmp_path / "pages.json").write_text(json.dumps([
        {"url": ENTRY}, {"url": FORM},
    ], ensure_ascii=False), encoding="utf-8")
    llm = FakeLLM([
        {"reason": "去未抓取页", "tool": "browser_navigate",
         "args": {"url": "https://demo.test/other"}},
        _end_decision(),
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    nav = outcome.steps[1]
    assert "越界被护栏拦截" in nav["result"]
    assert "不在允许页面集内" in nav["result"]


# ---- 3. 护栏开启：点击链接目标预检 ----

def test_guardrails_blocks_click_cross_domain_href(tmp_path):
    """点击 href 指向跨域的链接 → 执行前拦截，浏览器不发生跳转。"""
    llm = FakeLLM([
        {"reason": "点外域链接", "tool": "browser_click", "args": {"ref": "e2"}},
        _end_decision(),
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)
    click = outcome.steps[1]
    assert click["action"] == "browser_click"
    assert "越界被护栏拦截" in click["result"]
    assert browser.current == ENTRY, "点击必须被拦截在执行前，页面不得跳转"


def test_guardrails_allows_click_same_domain_href(tmp_path):
    """点击同域链接 → 正常执行（白名单不含 URL 集时同域 href 放行）。"""
    llm = FakeLLM([
        {"reason": "进商品管理", "tool": "browser_click", "args": {"ref": "e1"}},
        _end_decision(),
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)
    assert outcome.steps[1]["result"] == "ok"
    assert browser.current == FORM


# ---- 4. 护栏关闭（EXPLORE_GUARDRAILS=0）：回落旧逻辑 ----

def test_guardrails_off_old_domain_lock_message(tmp_path, monkeypatch):
    """开关关闭：跨域导航仍被旧域名锁拒绝，但消息是旧文案（无「护栏」字样）。"""
    monkeypatch.setenv("EXPLORE_GUARDRAILS", "0")
    llm = FakeLLM([
        {"reason": "想去外域", "tool": "browser_navigate", "args": {"url": EVIL}},
        _end_decision(),
    ])
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: FakeBrowser(_demo_pages(), ENTRY))
    nav = outcome.steps[1]
    assert "越域导航" in nav["result"]
    assert "护栏" not in nav["result"]


def test_guardrails_off_skips_click_href_check(tmp_path, monkeypatch):
    """开关关闭：点击跨域 href 的链接不做预检（旧行为，点击直接执行）。"""
    monkeypatch.setenv("EXPLORE_GUARDRAILS", "0")
    llm = FakeLLM([
        {"reason": "点外域链接", "tool": "browser_click", "args": {"ref": "e2"}},
        _end_decision(),
    ])
    browser = FakeBrowser(_demo_pages(), ENTRY)
    outcome = run_explore(ENTRY, out_dir=str(tmp_path), llm_client=llm,
                          browser_factory=lambda: browser)
    assert outcome.steps[1]["result"] == "ok", "关闭护栏后点击不做 href 预检"
    assert browser.current == EVIL
