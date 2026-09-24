"""web_crawler 单测：httpx 登录 hidden 字段保留、静态抓取、Playwright 降级路径。"""
import sys

import httpx
import pytest

from app.services import web_crawler
from app.services.web_crawler import (
    PageDesc,
    CrawlResult,
    _try_login,
    _chromium_launch_args,
    _explore_with_playwright,
    crawl,
    crawl_pages_json,
)

# ---- 测试用 HTML 夹具（模拟 DBERP 风格登录表单：含 CSRF hidden 字段） ----

LOGIN_HTML = """<html><head><title>登录</title></head><body>
<form method="post" action="/">
  <input type="text" name="admin_name" value="">
  <input type="password" name="admin_passwd" value="">
  <input type="hidden" name="login_csrf" value="csrf-token-abc">
  <input type="submit" name="submit-login" value="登 录">
</form></body></html>"""

INDEX_HTML = """<html><head><title>后台首页</title></head><body>
<ul class="menu"><li><a href="/goods">商品</a></li><li><a href="/order">订单</a></li></ul>
<h1>工作台</h1></body></html>"""

ENTRY_HTML = """<html><head><title>入口</title></head><body>
<a href="/page2">第二页</a>
<a href="/page3">第三页</a>
<a href="https://other.example.com/x">外域链接</a>
</body></html>"""


def _mk_client(monkeypatch, handler):
    """把 web_crawler 里的 httpx.AsyncClient 替换为带 MockTransport 的客户端（不发真实请求）。"""
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("verify", None)
        kwargs.pop("transport", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(web_crawler.httpx, "AsyncClient", factory)


# ---- 1. httpx 登录：hidden 字段必须保留原值（CSRF 修复回归） ----

@pytest.mark.asyncio
async def test_try_login_keeps_hidden_csrf(monkeypatch):
    captured: dict = {}
    state = {"posted": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.read().decode()
            state["posted"] = True
            return httpx.Response(200, text="<html><body>ok</body></html>")
        if state["posted"]:  # 登录后再拉首页：密码框消失 → 判定成功
            return httpx.Response(200, text=INDEX_HTML)
        return httpx.Response(200, text=LOGIN_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await _try_login(client, "https://demo.test/", {"username": "admin", "password": "123456"})

    assert ok is True
    assert "login_csrf=csrf-token-abc" in captured["body"], "hidden 字段被清空，CSRF 校验会失败"
    assert "admin_name=admin" in captured["body"]
    assert "admin_passwd=123456" in captured["body"]


@pytest.mark.asyncio
async def test_try_login_failed_when_form_persists(monkeypatch):
    """登录后密码框仍在 → 判定失败（如 CSRF 校验不过时服务端回登录页）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=LOGIN_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ok = await _try_login(client, "https://demo.test/", {"username": "admin", "password": "x"})
    assert ok is False


# ---- 2. 静态抓取路径（无凭据） ----

@pytest.mark.asyncio
async def test_crawl_static_bfs(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/":
            return httpx.Response(200, text=ENTRY_HTML, headers={"content-type": "text/html"})
        return httpx.Response(200, text=f"<html><head><title>页 {path}</title></head><body>内容</body></html>",
                              headers={"content-type": "text/html"})

    _mk_client(monkeypatch, handler)
    seen: list[PageDesc] = []
    result = await crawl("https://demo.test/", on_page=seen.append)

    assert isinstance(result, CrawlResult)
    assert [p.url for p in result.pages] == [
        "https://demo.test/", "https://demo.test/page2", "https://demo.test/page3"]
    assert result.login == "none"
    assert result.mode == "static"
    assert result.screenshots == {}
    assert len(seen) == 3  # on_page 每页回调一次


# ---- 3. Playwright 不可用 → 降级静态抓取（不崩） ----

@pytest.mark.asyncio
async def test_crawl_fallback_to_static_when_playwright_down(monkeypatch):
    """模拟 Playwright 探索不可用（返回 None）→ 降级 httpx 路径且登录仍生效。"""
    async def fake_explore(*args, **kwargs):
        return None

    captured: dict = {}
    state = {"posted": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.read().decode()
            state["posted"] = True
            return httpx.Response(200, text="<html><body>ok</body></html>")
        if state["posted"]:
            return httpx.Response(200, text=INDEX_HTML, headers={"content-type": "text/html"})
        return httpx.Response(200, text=LOGIN_HTML, headers={"content-type": "text/html"})

    monkeypatch.setattr(web_crawler, "_explore_with_playwright", fake_explore)
    _mk_client(monkeypatch, handler)

    result = await crawl("https://demo.test/", {"username": "admin", "password": "123456"})

    assert result.mode == "static"
    assert result.login == "success"
    assert "login_csrf=csrf-token-abc" in captured["body"], "降级路径 hidden 字段同样要保留原值"


@pytest.mark.asyncio
async def test_explore_returns_none_when_playwright_not_installed(monkeypatch):
    """未安装 playwright 包（import ImportError）→ 探索函数返回 None 而非抛异常。"""
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    result = await _explore_with_playwright(
        "https://demo.test/", {"username": "a", "password": "b"},
        "demo.test", 8, None, None)
    assert result is None


# ---- 4. root 运行时 Chromium 启动参数 ----

def test_chromium_args_no_sandbox_as_root(monkeypatch):
    monkeypatch.setattr(web_crawler.os, "geteuid", lambda: 0)
    assert _chromium_launch_args() == ["--no-sandbox"]


def test_chromium_args_normal_user(monkeypatch):
    monkeypatch.setattr(web_crawler.os, "geteuid", lambda: 501)
    assert _chromium_launch_args() == []


# ---- 5. PageDesc.screenshot 字段与序列化 ----

def test_page_desc_screenshot_field():
    p = PageDesc(url="https://demo.test/", screenshot="pages/page-001.png")
    data = __import__("json").loads(crawl_pages_json([p]))
    assert data[0]["screenshot"] == "pages/page-001.png"
    assert PageDesc(url="x").screenshot == ""
