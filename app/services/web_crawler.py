"""M1 网页浅抓取服务：同域 BFS 抓取页面结构，产出 PageDesc 喂给解析 Agent。

策略（对齐设计文档 3.3）：
- 有账密时优先走 Playwright 真实登录 + 同域探索：headless Chromium 打开入口页，
  检测到密码表单则真实填表提交（CSRF 自动带上），登录后 BFS 逐页抓取并截图；
  Playwright 未装 / 浏览器缺失 / 启动失败 → 优雅降级到 httpx 静态抓取（不崩）
- httpx + BeautifulSoup 同域 BFS：入口页 → 导航/a 链接去重，≤ CRAWL_MAX_PAGES 页
  （有凭据时用 httpx 会话提交登录，hidden 字段（如 CSRF token）保留原值提交）
- 每页抽取 PageDesc{url,title,headings,forms,buttons,links,nav_texts,text_digest,screenshot}
- 「可见文本 < 200 字且无表单」判定为 JS 渲染页 → Playwright headless 渲染降级
  （仅匿名路径；任何失败 try/except 优雅跳过，不崩）
"""
import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import urljoin, urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import CRAWL_MAX_PAGES, CRAWL_TIMEOUT

logger = logging.getLogger("services.web_crawler")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# JS 渲染判定阈值：可见文本字数
_TEXT_TOO_FEW = 200
# 登录后探索时排除的链接关键词（避免点退出登录把会话干掉）
_EXCLUDE_LINK_KEYWORDS = ("logout", "login", "signin", "signout", "退出", "注销")


class CrawlResult(NamedTuple):
    """crawl() 返回值：页面列表 + 截图映射 + 真实登录结果 + 抓取模式。

    - login："success" / "failed" / "none"（none = 未提供凭据，未尝试登录）
    - mode："playwright"（真实浏览器探索）/ "static"（httpx 静态抓取，含降级）
    - video：探索过程录屏（explore.webm）绝对路径；空串 = 无录屏（静态抓取 / 录屏失败）
    """
    pages: list["PageDesc"]
    screenshots: dict[str, str]
    login: str = "none"
    mode: str = "static"
    video: str = ""


@dataclass
class PageDesc:
    """单页结构化描述：喂给 ParserAgent 的最小充分信息。"""
    url: str
    title: str = ""
    headings: list[str] = field(default_factory=list)      # h1~h4 文本
    forms: list[dict] = field(default_factory=list)        # {action, method, fields:[{name,type,label}]}
    buttons: list[str] = field(default_factory=list)       # 按钮/提交控件文本
    links: list[str] = field(default_factory=list)         # 同域链接（绝对 URL，去重）
    nav_texts: list[str] = field(default_factory=list)     # 导航/菜单项文本
    text_digest: str = ""                                  # 可见文本摘要（前 500 字）
    rendered: bool = False                                 # 是否经 Playwright 渲染取得
    screenshot: str = ""                                   # 截图路径（相对任务目录），无则空串

    def to_markdown(self) -> str:
        """单页 → 结构化 Markdown 片段（供解析 Agent 阅读）。"""
        lines = [f"## 页面：{self.title or '(无标题)'}", f"URL：{self.url}"]
        if self.nav_texts:
            lines.append("导航菜单：" + "、".join(self.nav_texts[:20]))
        if self.headings:
            lines.append("内容标题：" + "；".join(self.headings[:20]))
        for f in self.forms:
            fields = "，".join(
                f"{fd.get('label') or fd.get('name') or '?'}({fd.get('type', 'text')})"
                for fd in f.get("fields", [])
            )
            lines.append(f"表单（{f.get('method', 'get').upper()} {f.get('action') or '当前页'}）：字段 {fields or '无'}")
        if self.buttons:
            lines.append("按钮：" + "、".join(self.buttons[:20]))
        if self.links:
            lines.append("链接：" + "；".join(self.links[:20]))
        if self.text_digest:
            lines.append("正文摘要：" + self.text_digest)
        return "\n".join(lines)


def pages_to_markdown(pages: list[PageDesc]) -> str:
    """整站抓取结果 → 一份 Markdown 文本（解析步骤的输入）。"""
    head = f"被测系统共抓取到 {len(pages)} 个页面，结构如下：\n"
    return head + "\n\n".join(p.to_markdown() for p in pages)


def _same_domain(url: str, base_host: str) -> bool:
    try:
        return urlparse(url).netloc == base_host
    except ValueError:
        return False


def _normalize(url: str) -> str:
    """去掉 fragment 的绝对 URL（BFS 去重键）。"""
    return urldefrag(url)[0]


def _extract_text(soup: BeautifulSoup) -> str:
    """可见文本（去 script/style），压平空白。"""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def _extract_form(form_tag) -> dict:
    """<form> → {action(绝对URL), method, fields:[{name,type,label}]}。"""
    fields: list[dict] = []
    for inp in form_tag.find_all(["input", "textarea", "select"]):
        itype = (inp.get("type") or ("textarea" if inp.name == "textarea"
                                     else "select" if inp.name == "select" else "text")).lower()
        name = inp.get("name") or inp.get("id") or ""
        label = inp.get("placeholder") or inp.get("aria-label") or ""
        if not label and name:
            # 找包裹/前置的 <label>
            lb = inp.find_previous("label")
            label = lb.get_text(" ", strip=True)[:30] if lb else ""
        fields.append({"name": name, "type": itype, "label": label[:30]})
    return {
        "action": form_tag.get("action") or "",
        "method": (form_tag.get("method") or "get").lower(),
        "fields": fields,
    }


def _parse_page(url: str, html: str, base_host: str, rendered: bool = False) -> tuple[PageDesc, list[str]]:
    """HTML → (PageDesc, 同域待抓链接列表)。"""
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:200]

    headings = [
        " ".join(t.get_text(" ", strip=True).split())[:100]
        for t in soup.find_all(["h1", "h2", "h3", "h4"])
        if t.get_text(strip=True)
    ]

    forms = [_extract_form(f) for f in soup.find_all("form")]

    buttons = []
    for b in soup.find_all("button"):
        if b.get_text(strip=True):
            buttons.append(b.get_text(" ", strip=True)[:40])
    for inp in soup.find_all("input", type=lambda v: v and v.lower() in ("submit", "button")):
        v = inp.get("value") or inp.get("aria-label") or ""
        if v:
            buttons.append(str(v)[:40])

    nav_texts: list[str] = []
    nav = soup.find("nav") or soup.find(attrs={"role": "navigation"}) or soup.find("ul", class_=lambda c: c and "menu" in c.lower())
    if nav:
        for a in nav.find_all("a"):
            txt = a.get_text(" ", strip=True)
            if txt:
                nav_texts.append(txt[:30])

    links: list[str] = []
    for a in soup.find_all("a", href=True):
        absu = _normalize(urljoin(url, a["href"]))
        if absu.startswith("http") and _same_domain(absu, base_host) and absu not in links:
            links.append(absu)

    text_digest = _extract_text(soup)[:500]
    page = PageDesc(url=url, title=title, headings=headings, forms=forms,
                    buttons=buttons, links=links, nav_texts=nav_texts,
                    text_digest=text_digest, rendered=rendered)
    return page, links


def _find_login_form_tag(soup: BeautifulSoup):
    """找含 password 输入的 <form> 原始标签；无则 None。"""
    for f in soup.find_all("form"):
        if f.find("input", attrs={"type": "password"}):
            return f
    return None


def _input_type(inp) -> str:
    """input/textarea/select 控件的类型推断（与 _extract_form 口径一致）。"""
    return (inp.get("type") or ("textarea" if inp.name == "textarea"
                                else "select" if inp.name == "select" else "text")).lower()


def _chromium_launch_args() -> list[str]:
    """按运行环境计算 Chromium 启动参数：root 下必须加 --no-sandbox，否则起不来。"""
    args: list[str] = []
    try:
        if os.geteuid() == 0:
            args.append("--no-sandbox")
    except AttributeError:  # 非 POSIX 平台（如 Windows）无 geteuid
        pass
    return args


async def _try_login(client: httpx.AsyncClient, entry_url: str,
                     credentials: dict) -> bool:
    """best-effort 表单登录：找到密码表单就按字段名注入账密提交。

    hidden 字段（如 CSRF token）保留页面原值提交，不能清空，否则服务端校验失败。
    成功判定（启发式）：响应 2xx 且登录后首页不再出现 password 输入框。
    失败不抛错——继续以匿名身份抓取（公开页面照样可测）。
    """
    username = credentials.get("username", "")
    password = credentials.get("password", "")
    if not password:
        return False
    try:
        r = await client.get(entry_url)
        soup = BeautifulSoup(r.text, "lxml")
        form_tag = _find_login_form_tag(soup)
        if not form_tag:
            return False
        data: dict[str, str] = {}
        name_given = pwd_given = False
        for inp in form_tag.find_all(["input", "textarea", "select"]):
            ftype = _input_type(inp)
            fname = inp.get("name") or inp.get("id") or ""
            if not fname:
                continue
            if ftype in ("text", "email", "tel") and not name_given:
                data[fname] = username
                name_given = True
            elif ftype == "password" and not pwd_given:
                data[fname] = password
                pwd_given = True
            elif ftype == "hidden":
                # 关键修复：hidden 字段保留原值（如 login_csrf），清空会导致 CSRF 校验失败
                data[fname] = inp.get("value") or ""
            # 其余类型（checkbox/submit/radio 等）不注入
        if not pwd_given:
            return False
        action = form_tag.get("action") or entry_url
        target = urljoin(entry_url, action)
        resp = await client.request((form_tag.get("method") or "post").upper(), target, data=data)
        if resp.status_code >= 400:
            return False
        # 登录成功启发式：当前会话再拉首页，密码框消失视为已登录
        r2 = await client.get(entry_url)
        return _find_login_form_tag(BeautifulSoup(r2.text, "lxml")) is None
    except Exception as e:  # noqa: BLE001  登录探测失败不影响主流程
        logger.warning("自动登录探测失败（%s），继续匿名抓取", e)
        return False


def _collect_video(tmp_dir: str | None, shots_base: Path | None) -> str:
    """把 Playwright 录屏临时目录里的 webm 归档为截图目录下 explore.webm；失败返回空串。

    video 文件在 context/browser 关闭后才落盘写完，本函数只在浏览器关闭后调用；
    任何异常（无录屏文件 / 移动失败）都吞掉返回 ""，不影响抓取主流程。
    """
    if not tmp_dir or shots_base is None:
        return ""
    try:
        vids = sorted(Path(tmp_dir).glob("*.webm"))
        if not vids:
            return ""
        dest = shots_base / "explore.webm"
        shutil.move(str(vids[0]), str(dest))
        logger.info("探索录屏已保存：%s", dest)
        return str(dest.resolve())
    except Exception as e:  # noqa: BLE001  录屏失败不影响主流程
        logger.warning("探索录屏保存失败：%s", e)
        return ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _explore_with_playwright(
    entry: str, credentials: dict, base_host: str, max_pages: int,
    on_page: Callable[[PageDesc], None] | None,
    screenshot_dir: str | None,
) -> CrawlResult | None:
    """Playwright 真实登录 + 同域 BFS 探索（有凭据时优先走）。

    流程：headless Chromium 打开入口页 → 检测密码表单则真实填表提交
    → 登录成功判定（密码框消失或 URL 离开登录页）→ 从当前页 BFS 收集同域链接
    → 逐页 goto 提取 PageDesc 并截图。

    返回 None 表示 Playwright 不可用（未装包 / 浏览器缺失 / 启动失败），
    调用方应降级到 httpx 静态抓取；其余失败同样捕获后返回 None，不向上抛。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.info("未安装 playwright 包，JS 渲染探索不可用，将降级静态抓取")
        return None

    # 截图目录：未提供则不截图
    shots_base: Path | None = None
    if screenshot_dir:
        try:
            shots_base = Path(screenshot_dir)
            shots_base.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("截图目录 %s 创建失败（%s），本次不截图", screenshot_dir, e)
            shots_base = None

    pages: list[PageDesc] = []
    screenshots: dict[str, str] = {}
    login = "none"
    video_path = ""

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=_chromium_launch_args())
            video_tmp: str | None = None
            try:
                ctx_opts: dict[str, Any] = {"user_agent": _UA}
                if shots_base is not None:
                    # 探索录屏（Playwright 标准 API，无需额外依赖）：录到临时目录，
                    # 浏览器关闭后统一归档为 screenshot_dir/explore.webm
                    video_tmp = tempfile.mkdtemp(prefix="aitf-crawl-video-")
                    ctx_opts["record_video_dir"] = video_tmp
                    ctx_opts["record_video_size"] = {"width": 1280, "height": 720}
                context = await browser.new_context(**ctx_opts)
                page = await context.new_page()

                # ---- 1. 打开入口页，真实登录 ----
                await page.goto(entry, timeout=CRAWL_TIMEOUT * 1000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:  # noqa: BLE001  部分页面常有长连接，networkidle 超时不致命
                    pass

                if credentials.get("password"):
                    login = "failed"
                    if await page.locator("input[type=password]").count() > 0:
                        # 账号输入：text/email 或无 type 的第一个文本框
                        user_loc = page.locator(
                            "input[type=text], input[type=email], input:not([type])").first
                        await user_loc.fill(credentials.get("username", ""))
                        await page.locator("input[type=password]").first.fill(credentials["password"])
                        btn = page.locator(
                            "input[type=submit], button[type=submit], "
                            "button:has-text('登录'), button:has-text('登 录')").first
                        if await btn.count() > 0:
                            await btn.click()
                        else:
                            await page.locator("input[type=password]").first.press("Enter")
                        try:
                            await page.wait_for_load_state("networkidle",
                                                           timeout=CRAWL_TIMEOUT * 1000)
                        except Exception:  # noqa: BLE001
                            pass
                        # 登录成功判定：密码框消失 或 URL 离开登录页
                        pwd_gone = await page.locator("input[type=password]").count() == 0
                        left_login = (urlparse(page.url).path.rstrip("/")
                                      != urlparse(entry).path.rstrip("/"))
                        if pwd_gone or left_login:
                            login = "success"
                            logger.info("Playwright 登录成功：%s", page.url)
                        else:
                            logger.warning("Playwright 登录未生效（密码框仍在登录页），按匿名继续探索")

                # ---- 2. 登录后从当前页 BFS 探索同域页面 ----
                visited: set[str] = set()
                queue: list[str] = [_normalize(page.url)]
                seq = 0
                while queue and len(pages) < max_pages:
                    cur = queue.pop(0)
                    if cur in visited:
                        continue
                    visited.add(cur)
                    try:
                        await page.goto(cur, timeout=CRAWL_TIMEOUT * 1000,
                                        wait_until="domcontentloaded")
                    except Exception as e:  # noqa: BLE001  单页打开失败跳过
                        logger.warning("Playwright 打开 %s 失败：%s", cur, e)
                        continue
                    try:
                        await page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:  # noqa: BLE001
                        pass

                    html = await page.content()
                    real_url = _normalize(page.url)
                    seq += 1
                    pg, links = _parse_page(real_url or cur, html, base_host, rendered=True)

                    # 截图落盘（序号命名稳定：page-001.png，与 pages 顺序一一对应）
                    if shots_base is not None:
                        shot_path = shots_base / f"page-{seq:03d}.png"
                        try:
                            await page.screenshot(path=str(shot_path), full_page=True)
                            screenshots[pg.url] = str(shot_path.resolve())
                            pg.screenshot = shot_path.name
                        except Exception as e:  # noqa: BLE001  截图失败不影响抓取
                            logger.warning("截图 %s 失败：%s", real_url, e)

                    pages.append(pg)
                    if on_page:
                        try:
                            on_page(pg)
                        except Exception:  # noqa: BLE001  回调异常不影响抓取
                            pass

                    # 收集同域链接（浏览器返回的 href 已是绝对 URL）
                    try:
                        hrefs = await page.eval_on_selector_all(
                            "a[href]", "els => els.map(e => e.href)")
                    except Exception:  # noqa: BLE001
                        hrefs = []
                    for h in hrefs:
                        nk = _normalize(h)
                        if not nk.startswith("http") or not _same_domain(nk, base_host):
                            continue
                        low = nk.lower()
                        if any(kw in low for kw in _EXCLUDE_LINK_KEYWORDS):
                            continue  # 避开退出登录/登录页自身
                        if nk not in visited and len(visited) < max_pages * 3:
                            queue.append(nk)
            finally:
                await browser.close()
                # video 在 context/browser close 后才写完，此刻再归档（失败返回 ""）
                video_path = _collect_video(video_tmp, shots_base)
    except Exception as e:  # noqa: BLE001  浏览器缺失 / 启动失败等 → 降级
        logger.warning("Playwright 探索不可用（%s），已降级静态抓取", e)
        return None

    return CrawlResult(pages=pages, screenshots=screenshots, login=login,
                       mode="playwright", video=video_path)


async def _render_pages(urls: list[str]) -> dict[str, str]:
    """Playwright headless 渲染静态抓不到的页面 → {url, html}。

    任何失败（未装浏览器 / 未装包 / 超时）都返回 {}，调用方回退静态结果。
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.info("未安装 playwright，跳过 JS 渲染降级")
        return {}
    out: dict[str, str] = {}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=_chromium_launch_args())
            try:
                page = await browser.new_page(user_agent=_UA)
                for u in urls[:CRAWL_MAX_PAGES]:
                    try:
                        await page.goto(u, timeout=CRAWL_TIMEOUT * 1000, wait_until="networkidle")
                        out[u] = await page.content()
                    except Exception as e:  # noqa: BLE001  单页失败跳过
                        logger.warning("Playwright 渲染 %s 失败：%s", u, e)
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001  未执行 playwright install chromium 等
        logger.warning("Playwright 渲染不可用（%s），回退静态抓取结果", e)
    return out


async def crawl(url: str, credentials: dict | None = None,
                max_pages: int | None = None,
                on_page: Callable[[PageDesc], None] | None = None,
                screenshot_dir: str | None = None) -> CrawlResult:
    """浅抓入口 URL 同域 ≤ max_pages 页，返回 CrawlResult(pages, screenshots, login, mode)。

    - credentials：{"username","password"}，有值时优先走 Playwright 真实登录 + 探索 + 截图；
      Playwright 不可用则降级 httpx 静态抓取（日志标注「JS 渲染探索不可用，已降级静态抓取」）
    - screenshot_dir：截图落盘目录（绝对路径更佳）；None 则不截图
    - on_page(page: PageDesc)：每抓完一页的回调（引擎用来更新 StepLog.progress）
    - 全程网络异常向上抛（引擎把 crawler 步骤标 failed + 中文错误文案）
    """
    max_pages = max_pages or CRAWL_MAX_PAGES
    entry = _normalize(url if "://" in url else f"https://{url}")
    base_host = urlparse(entry).netloc
    if not base_host:
        raise ValueError(f"非法 URL：{url}")

    creds = credentials or {}

    # ---- 路径 1：有凭据 → Playwright 真实登录探索；不可用则降级 ----
    if creds.get("password"):
        pw_result = await _explore_with_playwright(
            entry, creds, base_host, max_pages, on_page, screenshot_dir)
        if pw_result is not None:
            return pw_result
        logger.warning("JS 渲染探索不可用，已降级静态抓取（%s）", entry)

    # ---- 路径 2：httpx 静态抓取（无凭据 / Playwright 降级共用） ----
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=CRAWL_TIMEOUT,
        headers={"User-Agent": _UA}, verify=True,
    ) as client:
        login = "none"
        if creds:
            ok = await _try_login(client, entry, creds)
            login = "success" if ok else "failed"
            if not ok:
                logger.info("未检测到登录表单或登录未生效，按匿名方式抓取")

        pages: list[PageDesc] = []
        visited: set[str] = set()
        queue = [entry]
        while queue and len(pages) < max_pages:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            r = await client.get(cur)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "text/html" not in ctype and "xml" not in ctype:
                continue
            page, links = _parse_page(cur, r.text, base_host)
            pages.append(page)
            if on_page:
                try:
                    on_page(page)
                except Exception:  # noqa: BLE001  回调异常不影响抓取
                    pass
            for lk in links:
                if lk not in visited and len(visited) < max_pages * 3:
                    queue.append(lk)

        # JS 渲染降级：可见文本过少且无表单的页面 → Playwright 重抓（匿名路径）
        stale = [p.url for p in pages
                 if len(p.text_digest) < _TEXT_TOO_FEW and not p.forms and not p.nav_texts]
        if stale:
            rendered_html = await _render_pages(stale)
            for i, p in enumerate(pages):
                if p.url in rendered_html:
                    np, _ = _parse_page(p.url, rendered_html[p.url], base_host, rendered=True)
                    pages[i] = np

    return CrawlResult(pages=pages, screenshots={}, login=login, mode="static")


def crawl_pages_json(pages: list[PageDesc]) -> str:
    """PageDesc 列表 → JSON 文本（落 target.pages_json / 任务目录缓存）。"""
    return json.dumps([asdict(p) for p in pages], ensure_ascii=False)


def run_crawl_sync(url: str, credentials: dict | None = None,
                   max_pages: int | None = None, on_page=None,
                   screenshot_dir: str | None = None) -> CrawlResult:
    """同步包装（工作流引擎在后台线程运行，无事件循环冲突）。"""
    return asyncio.run(crawl(url, credentials, max_pages, on_page, screenshot_dir))
