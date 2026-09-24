"""M5 探索式测试 Agent（V3 ReAct 感知-决策循环，设计文档第 5 节）。

零文档站点：只给 URL（+可选账密），LLM 持浏览器工具集自主探索——
感知页面（accessibility 摘要）→ 决策测什么 → 生成用例 →（复用 M2）生成脚本。

ReAct 循环：
    while not done and steps < MAX_STEPS:
        obs   = browser.snapshot()            # 可交互元素摘要 role/name/ref（省 token，不返原始 HTML）
        plan  = llm.decide(history, obs, goal) # LLM 每轮输出一个 JSON 决策
        result = execute_tool(plan)            # 沙箱内执行，域名锁定 + 危险操作黑名单
        history.append(plan, result)
        LLM 认为功能流探索充分 → submit_cases 提交用例（可多次）

决策协议说明：llm_service 客户端（LangChainClient）未透传 OpenAI tools 参数，
且本模块不改 llm_service（并行代理领地），故采用等价的 JSON 决策协议——
模型每轮严格输出 {"reason", "tool", "args"}，由本模块解析后分发工具。
任何 OpenAI 兼容端点均可运行，不依赖端点原生 function calling。

护栏（设计文档 5.4）：
- 域名锁定：越域导航/跳转直接拒绝，拒绝原因回喂 LLM
- 危险操作黑名单：按钮/链接文案含「删除/支付/提交订单/退款/注销/清空」等 → 一期直接拒绝执行
- 预算控制：EXPLORE_MAX_STEPS / EXPLORE_MAX_TOKENS / EXPLORE_MAX_SECONDS（os.getenv 直读，
  不改 config.py）；超限优雅收敛，已提交用例照常走下游 scripter → exporter

每步落 StepLog 由引擎负责（name=explore）：details 含当前 URL、动作、LLM 理由、
截图相对路径（任务输出目录 explore/step-NNN.png，前端 Wave B 渲染探索时间线）。
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("services.explorer_agent")

# 副作用导入：pipeline_lib 会把 generator_core 注入 sys.path（_normalize_case 需要
# src.models.testcase.align_step_expectations；与 sample_seeder 同一惯例）
from app.services import pipeline_lib  # noqa: F401,E402

# ---- 预算配置：os.getenv 直读（调用时读取，测试可 monkeypatch；不改 config.py） ----
def _max_steps() -> int:
    try:
        return max(1, int(os.getenv("EXPLORE_MAX_STEPS", "30")))
    except ValueError:
        return 30


def _max_tokens() -> int:
    try:
        return max(1, int(os.getenv("EXPLORE_MAX_TOKENS", "150000")))
    except ValueError:
        return 150000


def _max_seconds() -> float:
    try:
        return max(30.0, float(os.getenv("EXPLORE_MAX_SECONDS", "900")))
    except ValueError:
        return 900.0


# 危险操作黑名单：按钮/链接文案命中即拒绝点击（一期从宽，设计文档 5.4）
DANGEROUS_KEYWORDS = ("删除", "支付", "提交订单", "退款", "注销", "清空",
                      "logout", "signout", "delete", "refund")

# 用例字段合法值（对齐 src/models/testcase.py 的枚举）
_CASE_TYPES = ("正向", "异常", "边界值", "场景组合")
_PRIORITIES = ("P0", "P1", "P2", "P3")

# 快照元素上限（防超长页面把上下文打爆）
_SNAPSHOT_MAX_ELEMENTS = 60


# ============ 浏览器会话接口 ============

class BrowserSession:
    """真实浏览器会话（Playwright sync API）。

    引擎在后台线程运行 run_task（无事件循环冲突），直接用 sync_playwright。
    与测试用 FakeBrowser 共享同一方法协议：
        goto/click/fill/screenshot/back → (ok, msg)
        snapshot → (text, registry)     registry: ref → {"role","name","css"}
        try_login(credentials) → "none" | "success" | "failed"
        save_storage_state(path) → 路径（失败返回 ""）
    """

    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        # root 下必须加 --no-sandbox（复用 web_crawler 的启动参数计算）
        from app.services.web_crawler import _chromium_launch_args
        self._browser = self._pw.chromium.launch(headless=headless,
                                                 args=_chromium_launch_args())
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._registry: dict[str, dict] = {}

    @property
    def url(self) -> str:
        return self._page.url

    @property
    def title(self) -> str:
        try:
            return self._page.title()
        except Exception:  # noqa: BLE001
            return ""

    def goto(self, url: str) -> tuple[bool, str]:
        try:
            self._page.goto(url, timeout=20000, wait_until="domcontentloaded")
            try:
                self._page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001  长连接页面 networkidle 超时不致命
                pass
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"打开失败：{str(e)[:120]}"

    def back(self) -> tuple[bool, str]:
        try:
            self._page.go_back(timeout=15000)
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"后退失败：{str(e)[:120]}"

    # 快照 JS：收集可交互元素 → role/name/ref/css 定位路径（不返回原始 HTML）
    _SNAPSHOT_JS = """() => {
        const sel = 'a[href], button, input, select, textarea, '
            + '[role="button"], [role="link"], [role="tab"], [role="menuitem"], [onclick]';
        const els = Array.from(document.querySelectorAll(sel));
        function name(el) {
            if (el.tagName === 'INPUT') {
                const t = (el.type || 'text').toLowerCase();
                if (t === 'submit' || t === 'button') {
                    return el.value || el.getAttribute('aria-label') || '';
                }
                return el.placeholder || el.getAttribute('aria-label') || el.name || el.id || t;
            }
            return (el.innerText || el.value || el.getAttribute('aria-label')
                || el.title || '').trim();
        }
        function role(el) {
            const r = el.getAttribute('role');
            if (r) return r;
            const tag = el.tagName.toLowerCase();
            if (tag === 'a') return 'link';
            if (tag === 'button') return 'button';
            if (tag === 'select') return 'combobox';
            if (tag === 'textarea') return 'textbox';
            if (tag === 'input') {
                const t = (el.type || 'text').toLowerCase();
                if (t === 'checkbox') return 'checkbox';
                if (t === 'radio') return 'radio';
                if (t === 'submit' || t === 'button') return 'button';
                return 'textbox';
            }
            return tag;
        }
        function cssPath(el) {
            const parts = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && parts.length < 8) {
                let p = cur.tagName.toLowerCase();
                if (cur.id) { parts.unshift(p + '#' + CSS.escape(cur.id)); break; }
                const parent = cur.parentElement;
                if (parent) {
                    const same = Array.from(parent.children)
                        .filter(c => c.tagName === cur.tagName);
                    if (same.length > 1) {
                        p += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
                    }
                }
                parts.unshift(p);
                cur = parent;
            }
            return parts.join(' > ');
        }
        const out = [];
        let i = 0;
        for (const el of els) {
            if (out.length >= __MAX__) break;
            if (!el.offsetParent && el.tagName !== 'BODY') continue;
            const nm = name(el).replace(/\\s+/g, ' ').trim().slice(0, 40);
            const tag = el.tagName.toLowerCase();
            if (!nm && !['input', 'select', 'textarea'].includes(tag)) continue;
            i += 1;
            out.push({ref: 'e' + i, role: role(el), name: nm, css: cssPath(el)});
        }
        return out;
    }"""

    def snapshot(self) -> tuple[str, dict[str, dict]]:
        """返回 (可交互元素摘要文本, ref→元素信息注册表)。"""
        js = self._SNAPSHOT_JS.replace("__MAX__", str(_SNAPSHOT_MAX_ELEMENTS))
        try:
            els = self._page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            return f"(快照失败：{str(e)[:100]})", {}
        self._registry = {}
        lines = [f"URL：{self.url}", f"标题：{self.title or '(无标题)'}"]
        for el in els:
            ref = el["ref"]
            self._registry[ref] = {"role": el["role"], "name": el["name"], "css": el["css"]}
            lines.append(f"[{ref}] {el['role']} {el['name']}".rstrip())
        return "\n".join(lines), dict(self._registry)

    def _locate(self, ref: str):
        css = (self._registry.get(ref) or {}).get("css")
        if not css:
            return None
        return self._page.locator(css).first

    def click(self, ref: str) -> tuple[bool, str]:
        loc = self._locate(ref)
        if loc is None:
            return False, f"ref {ref} 无效（请先 browser_snapshot 获取最新元素列表）"
        try:
            loc.click(timeout=8000)
            try:
                self._page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"点击失败：{str(e)[:120]}"

    def fill(self, fields: list[dict]) -> tuple[bool, str]:
        if not isinstance(fields, list) or not fields:
            return False, "fields 必须是非空数组 [{ref, value}]"
        errors: list[str] = []
        for f in fields:
            ref = str(f.get("ref") or "")
            value = str(f.get("value") or "")
            loc = self._locate(ref)
            if loc is None:
                errors.append(f"{ref} 无效")
                continue
            try:
                loc.fill(value, timeout=8000)
            except Exception as e:  # noqa: BLE001  单字段失败记录后继续
                errors.append(f"{ref} 填写失败：{str(e)[:80]}")
        if errors:
            return False, "；".join(errors[:5])
        return True, "ok"

    def screenshot(self, path: str) -> tuple[bool, str]:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=path, full_page=True)
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"截图失败：{str(e)[:120]}"

    def try_login(self, credentials: dict) -> str:
        """首步自动登录（思路复用 web_crawler._explore_with_playwright 的登录段）。"""
        username = credentials.get("username", "")
        password = credentials.get("password", "")
        if not password:
            return "none"
        try:
            if self._page.locator("input[type=password]").count() == 0:
                return "none"   # 当前页无登录表单，按匿名继续
            entry_path = urlparse(self.url).path.rstrip("/")
            user_loc = self._page.locator(
                "input[type=text], input[type=email], input:not([type])").first
            user_loc.fill(username)
            self._page.locator("input[type=password]").first.fill(password)
            btn = self._page.locator(
                "input[type=submit], button[type=submit], "
                "button:has-text('登录'), button:has-text('登 录')").first
            if btn.count() > 0:
                btn.click()
            else:
                self._page.locator("input[type=password]").first.press("Enter")
            try:
                self._page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
            pwd_gone = self._page.locator("input[type=password]").count() == 0
            left_login = urlparse(self.url).path.rstrip("/") != entry_path
            return "success" if (pwd_gone or left_login) else "failed"
        except Exception as e:  # noqa: BLE001
            logger.warning("探索登录失败：%s", e)
            return "failed"

    def save_storage_state(self, path: str) -> str:
        """登录态落盘（scripter 生成的 conftest 可经 AITF_STORAGE_STATE 复用）。"""
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=path)
            return path
        except Exception as e:  # noqa: BLE001
            logger.warning("storage_state 保存失败：%s", e)
            return ""

    def close(self) -> None:
        for closer in (self._context, self._browser):
            try:
                closer.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._pw.stop()
        except Exception:  # noqa: BLE001
            pass


# ============ ReAct 运行结果 ============

@dataclass
class ExploreOutcome:
    """run_explore 返回值：用例 + 步骤日志 + 摘要 + 收敛原因。"""
    cases: list = field(default_factory=list)          # 结构化用例（现有 case schema）
    steps: list = field(default_factory=list)          # 每步明细（前端时间线数据源）
    details: dict = field(default_factory=dict)        # StepLog.input_summary 的 JSON 对象
    summary: str = ""                                  # StepLog.output_summary
    stop_reason: str = "done"                          # done / max_steps / token_budget / time_budget / llm_unavailable / error
    login: str = "none"                                # none / success / failed
    page_obs: dict = field(default_factory=dict)       # url → 最近一次快照文本（pages_md 素材）


# ============ 决策解析与护栏 ============

def _strip_fences(raw: str) -> str:
    """剥掉模型偶发输出的 markdown 围栏与首尾空白。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def parse_decision(raw: str) -> dict:
    """LLM 输出 → {"reason","tool","args"}；解析失败抛 ValueError。"""
    text = _strip_fences(raw)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中不含 JSON 对象")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict) or not str(obj.get("tool") or "").strip():
        raise ValueError("JSON 缺少 tool 字段")
    return obj


def _normalize_case(c: Any) -> dict | None:
    """LLM 提交的用例 → 现有 case schema 的 dict（非法字段兜底，缺标题丢弃）。"""
    if not isinstance(c, dict):
        return None
    title = str(c.get("title") or "").strip()
    if not title:
        return None
    steps = [str(s).strip() for s in (c.get("steps") or []) if str(s).strip()]
    expected = str(c.get("expected") or "").strip()
    # 逐步预期兜底（与 TestCase.resolved_expectations 同规则）
    from src.models.testcase import align_step_expectations
    expectations = align_step_expectations(steps, c.get("step_expectations"), expected)
    ct = str(c.get("case_type") or "").strip()
    pr = str(c.get("priority") or "").strip()
    return {
        "case_id": "",
        "title": title,
        "module": str(c.get("module") or "").strip() or "探索用例",
        "case_type": ct if ct in _CASE_TYPES else "正向",
        "priority": pr if pr in _PRIORITIES else "P1",
        "pre_condition": str(c.get("pre_condition") or "").strip(),
        "steps": steps,
        "step_expectations": expectations,
        "expected": expected,
        "test_data": str(c.get("test_data") or "").strip() or None,
    }


def is_dangerous(text: str) -> str:
    """元素文案命中黑名单 → 返回命中的关键词；否则返回空串。"""
    low = (text or "").lower()
    for kw in DANGEROUS_KEYWORDS:
        if kw in low:
            return kw
    return ""


def explore_pages_md(entry_url: str, outcome: ExploreOutcome) -> str:
    """探索结果 → 页面结构 Markdown（scripter 生成脚本时的定位器上下文）。"""
    lines = [f"被测系统 {entry_url} 经探索 Agent 实际访问，页面结构如下："]
    for url, obs in outcome.page_obs.items():
        lines.append("")
        lines.append(f"## 页面：{url}")
        lines.append(obs)
    if len(lines) == 1:
        lines.append("（探索未取得页面快照，按用例语义选择定位器）")
    return "\n".join(lines)


# ============ 决策提示词 ============

_SYSTEM_PROMPT = """你是探索式测试 Agent（ReAct 模式），目标站点：{url}。任务：{goal}

每轮你必须只输出一个 JSON 对象（不要 markdown 围栏、不要任何解释文字）：
{{"reason": "一句话理由", "tool": "工具名", "args": {{...}}}}

可用工具：
- browser_navigate {{"url": "..."}} —— 跳转页面（仅允许 {host} 域内）
- browser_click {{"ref": "eN"}} —— 点击元素（ref 必须来自最近一次快照）
- browser_fill {{"fields": [{{"ref": "eN", "value": "..."}}]}} —— 批量填写表单
- browser_snapshot {{}} —— 获取当前页面可交互元素列表
- browser_screenshot {{}} —— 截图存档
- browser_back {{}} —— 浏览器后退
- submit_cases {{"cases": [...], "done": true}} —— 提交测试用例；done=true 表示探索结束

用例字段（cases 数组元素）：title（动作概括）、module、case_type（正向|异常|边界值|场景组合）、
priority（P0|P1|P2|P3）、pre_condition、steps（步骤数组）、step_expectations（逐步预期数组）、
expected（整体预期）、test_data。

探索策略：先 snapshot 了解页面 → 按功能流逐个探索（导航/填表/点击）→ 每探索完一个功能流
立即 submit_cases 提交该流的用例（可多次提交）→ 所有功能流探索完后 submit_cases(done=true) 结束。
注意：删除/支付/提交订单/退款/注销/清空类危险操作会被系统拒绝，不要尝试也不要为其提交执行类用例。
重要：不要重复执行已经做过的相同动作（同一工具+同样参数做过一次就不要再做）；
对同一控件的不同测试数据，直接写进 submit_cases 的用例里，而不是反复填表验证；
已经完成第 {max_steps_hint} 步仍无新发现时，必须尽快 submit_cases 结束。"""


def _build_decision_messages(goal: str, entry_url: str, host: str,
                             history: list[str], obs: str,
                             step_no: int = 0, max_steps: int = 0) -> list[dict]:
    """组装决策上下文：system + 截断的 history + 当前观察。"""
    recent = history[-12:]  # 截断防 token 爆炸
    hist_text = "\n".join(recent) if recent else "（暂无历史动作）"
    user = (f"【历史动作与结果】\n{hist_text}\n\n【当前页面观察】\n{obs}\n\n"
            f"【进度】当前第 {step_no}/{max_steps} 步。"
            f"剩余步数不多时请直接 submit_cases 汇总用例并结束。\n"
            f"请输出下一个动作的 JSON。")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT.format(
            url=entry_url, goal=goal, host=host, max_steps_hint=max_steps)},
        {"role": "user", "content": user[:12000]},
    ]


# ============ ReAct 主循环 ============

def run_explore(entry_url: str, credentials: dict | None = None,
                out_dir: str | None = None, llm_client: Any = None,
                goal: str = "", progress_cb=None,
                browser_factory: Any = None) -> ExploreOutcome:
    """探索式测试主入口：ReAct 循环产出用例。

    入参：
        entry_url       ：被测系统入口 URL（域名锁定基准）
        credentials     ：可选 {"username","password"}，有账密首步先登录
        out_dir         ：任务输出目录（截图落 out_dir/explore/step-NNN.png）
        llm_client      ：有 .chat(messages, **kw) 的客户端；None → 不启动浏览器直接收敛
        goal            ：探索目标描述（任务名）
        progress_cb     ：可选回调(str)，每步更新 StepLog.progress
        browser_factory ：可注入浏览器工厂（测试用 FakeBrowser）；缺省 PlaywrightBrowser

    出参：ExploreOutcome（cases 已按现有 case schema 归一化，引擎接 scripter → exporter）
    """
    outcome = ExploreOutcome()
    entry_url = (entry_url or "").strip()
    if not entry_url:
        outcome.stop_reason = "error"
        outcome.summary = "缺少被测系统地址"
        return outcome
    if llm_client is None:
        # 模型不可用：优雅收敛（不启动浏览器），已提交用例为空照常走下游
        outcome.stop_reason = "llm_unavailable"
        outcome.summary = "未配置可用模型，探索未执行（请先在设置中配置模型后重试）"
        return outcome

    parsed = urlparse(entry_url)
    entry_host = parsed.netloc
    entry_scheme = parsed.scheme
    max_steps = _max_steps()
    max_tokens = _max_tokens()
    max_seconds = _max_seconds()
    shots_dir = Path(out_dir) / "explore" if out_dir else None
    if shots_dir:
        shots_dir.mkdir(parents=True, exist_ok=True)

    est_tokens = 0
    t0 = time.monotonic()
    history: list[str] = []
    steps: list[dict] = []
    storage_state = ""

    def _allow_url(url: str) -> tuple[bool, str]:
        """域名锁定：仅允许入口域（file:// 入口允许任意 file:// 页面）。"""
        try:
            p = urlparse(url)
        except ValueError:
            return False, f"非法 URL：{url}"
        if p.scheme not in ("http", "https", "file"):
            return False, f"非法协议 {p.scheme or '(空)'}，仅允许 http/https"
        if p.scheme == "file":
            if entry_scheme != "file":
                return False, "已拒绝：本地文件协议不允许（仅限远程站点）"
            return True, "ok"
        if not p.netloc or p.netloc != entry_host:
            return False, f"已拒绝：越域导航 {url}（域名锁定，仅允许 {entry_host}）"
        return True, "ok"

    def _add_step(url: str, action: str, args: Any, reason: str,
                  ok: bool, msg: str, shot_rel: str = "") -> None:
        """记录一步 + 更新进度。"""
        rec = {"n": len(steps) + 1, "url": url, "action": action,
               "args": args, "reason": reason, "result": ("ok" if ok else f"拒绝/失败：{msg}"),
               "screenshot": shot_rel}
        steps.append(rec)
        outcome.steps = steps
        if progress_cb:
            try:
                mark = "" if ok else "⚠ "
                progress_cb(f"第 {rec['n']}/{max_steps} 步 · {mark}{action} · "
                            f"{url[:80]} · {reason[:60]}")
            except Exception:  # noqa: BLE001  进度回调失败不影响主流程
                pass

    def _shot(browser: Any) -> str:
        """截图到 explore/step-NNN.png，返回相对任务目录路径；失败返回空串。"""
        if shots_dir is None:
            return ""
        path = shots_dir / f"step-{len(steps) + 1:03d}.png"
        ok, _ = browser.screenshot(str(path))
        return f"explore/{path.name}" if ok else ""

    browser = None
    try:
        browser = (browser_factory or BrowserSession)()
    except Exception as e:  # noqa: BLE001  Playwright 未装 / 浏览器缺失
        outcome.stop_reason = "error"
        outcome.summary = f"浏览器不可用：{str(e)[:120]}"
        outcome.details = {"url": entry_url, "error": outcome.summary, "steps": []}
        return outcome

    try:
        # ---- 第 0 步：打开入口页（域名锁定基准），有账密先登录 ----
        ok, msg = browser.goto(entry_url)
        _add_step(browser.url, "browser_navigate", {"url": entry_url},
                  "打开入口页", ok, msg, _shot(browser))
        if not ok:
            outcome.stop_reason = "error"
            outcome.summary = f"入口页打开失败：{msg}"
            outcome.details = {"url": entry_url, "login": "none",
                               "steps": steps, "cases_submitted": 0, "stop_reason": "error"}
            return outcome

        if credentials and credentials.get("password"):
            outcome.login = browser.try_login(credentials)
            _add_step(browser.url, "browser_login",
                      {"username": credentials.get("username", "")},
                      "自动登录被测系统", outcome.login == "success",
                      "登录成功" if outcome.login == "success" else "登录未生效，按匿名继续",
                      _shot(browser))
            if outcome.login == "success" and shots_dir is not None:
                storage_state = browser.save_storage_state(str(shots_dir / "storage_state.json"))

        # ---- ReAct 主循环 ----
        step_no = 0
        last_action_key = ""
        repeat_count = 0
        while step_no < max_steps:
            # 预算检查（步数循环条件之外还有 token / 时间两条线）
            if est_tokens >= max_tokens:
                outcome.stop_reason = "token_budget"
                break
            if time.monotonic() - t0 >= max_seconds:
                outcome.stop_reason = "time_budget"
                break

            step_no += 1
            obs, registry = browser.snapshot()
            if browser.url not in outcome.page_obs:
                outcome.page_obs[browser.url] = obs

            messages = _build_decision_messages(goal, entry_url,
                                                entry_host or entry_scheme, history, obs,
                                                step_no, max_steps)
            try:
                raw = llm_client.chat(messages, temperature=0.2, max_tokens=2048)
            except Exception as e:  # noqa: BLE001  LLM 网络失败 → 收敛不崩
                _add_step(browser.url, "llm_error", {}, "调用决策模型", False,
                          f"{e.__class__.__name__}: {str(e)[:120]}")
                outcome.stop_reason = "error"
                break
            est_tokens += (sum(len(str(m["content"])) for m in messages) + len(raw or "")) // 2

            try:
                plan = parse_decision(raw)
            except (ValueError, json.JSONDecodeError) as e:
                reason = f"决策解析失败（{e}），请严格只输出 JSON 对象"
                _add_step(browser.url, "parse_error", {"raw": (raw or "")[:200]},
                          "解析 LLM 决策", False, reason)
                history.append(f"[决策无效] {reason}")
                continue

            action = str(plan.get("tool") or "").strip()
            args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
            reason = str(plan.get("reason") or "").strip()
            done_flag = bool(plan.get("done") or args.get("done"))

            # 防重复护栏：连续 3 次完全相同的动作 → 注入收敛警告（模型卡死时兜底）
            action_key = f"{action}|{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            repeat_count = repeat_count + 1 if action_key == last_action_key else 1
            last_action_key = action_key
            if repeat_count == 3:
                history.append("【系统警告】已连续 3 次执行完全相同的动作。"
                               "请改变探索目标；若功能流已探索充分，立即 submit_cases 提交用例并结束。")

            # ---- 工具分发（护栏内执行） ----
            if action == "browser_navigate":
                url = str(args.get("url") or "")
                allowed, deny = _allow_url(url)
                if not allowed:
                    _add_step(browser.url, action, args, reason, False, deny)
                    history.append(f"[{action}] {url} → {deny}（请继续探索 {entry_host or entry_scheme} 域内页面）")
                else:
                    ok, msg = browser.goto(url)
                    shot = _shot(browser)
                    _add_step(browser.url, action, args, reason, ok, msg, shot)
                    history.append(f"[{action}] {url} → {'ok' if ok else msg}")

            elif action == "browser_click":
                ref = str(args.get("ref") or "")
                info = registry.get(ref)
                if info is None:
                    deny = f"ref {ref} 无效（快照里不存在，请先 browser_snapshot）"
                    _add_step(browser.url, action, args, reason, False, deny)
                    history.append(f"[{action}] {ref} → {deny}")
                else:
                    kw = is_dangerous(info.get("name", ""))
                    if kw:
                        deny = f"已拒绝：命中危险操作黑名单（「{kw}」），一期禁止执行该点击"
                        _add_step(browser.url, action, args, reason, False, deny)
                        history.append(f"[{action}] {ref}({info.get('name')}) → {deny}")
                    else:
                        ok, msg = browser.click(ref)
                        shot = _shot(browser)
                        _add_step(browser.url, action, args, reason, ok, msg, shot)
                        history.append(f"[{action}] {ref}({info.get('name')}) → {'ok' if ok else msg}")

            elif action == "browser_fill":
                fields = args.get("fields")
                fields = fields if isinstance(fields, list) else []
                bad = [str(f.get("ref")) for f in fields
                       if not isinstance(f, dict) or str(f.get("ref") or "") not in registry]
                if not fields:
                    deny = "fields 为空，需 [{ref, value}] 数组"
                    _add_step(browser.url, action, args, reason, False, deny)
                    history.append(f"[{action}] → {deny}")
                elif bad:
                    deny = f"ref 无效：{','.join(bad[:5])}（请先 browser_snapshot）"
                    _add_step(browser.url, action, args, reason, False, deny)
                    history.append(f"[{action}] → {deny}")
                else:
                    ok, msg = browser.fill(fields)
                    shot = _shot(browser)
                    _add_step(browser.url, action, args, reason, ok, msg, shot)
                    history.append(f"[{action}] {len(fields)} 个字段 → {'ok' if ok else msg}")

            elif action == "browser_snapshot":
                shot = _shot(browser)
                _add_step(browser.url, action, args, reason, True, f"快照 {len(registry)} 个元素", shot)
                history.append(f"[{action}] → {len(registry)} 个可交互元素")

            elif action == "browser_screenshot":
                shot = _shot(browser)
                _add_step(browser.url, action, args, reason, bool(shot),
                          "已截图" if shot else "截图失败", shot)
                history.append(f"[{action}] → {'ok' if shot else '截图失败'}")

            elif action == "browser_back":
                ok, msg = browser.back()
                shot = _shot(browser)
                _add_step(browser.url, action, args, reason, ok, msg, shot)
                history.append(f"[{action}] → {'ok' if ok else msg}")

            elif action == "submit_cases":
                cases_raw = args.get("cases") if isinstance(args.get("cases"), list) else []
                normalized = [c for c in (_normalize_case(x) for x in cases_raw) if c]
                outcome.cases.extend(normalized)
                shot = _shot(browser)
                _add_step(browser.url, action,
                          {"count": len(normalized)}, reason, True,
                          f"提交 {len(normalized)} 条用例（累计 {len(outcome.cases)} 条）", shot)
                history.append(f"[{action}] 提交 {len(normalized)} 条用例（累计 {len(outcome.cases)} 条）")
                if done_flag:
                    outcome.stop_reason = "done"
                    break
                history.append("（done=false：可继续探索其他功能流，结束时请 submit_cases 且 done=true）")

            else:
                deny = f"未知工具 {action}，可用工具见系统提示"
                _add_step(browser.url, action, args, reason, False, deny)
                history.append(f"[{action}] → {deny}")

            if done_flag and action != "submit_cases":
                # LLM 在非 submit_cases 工具上标记 done：尊重其结束意愿
                outcome.stop_reason = "done"
                break
        else:
            outcome.stop_reason = "max_steps"

        # 循环因 break 跳出但未达 submit_cases done 时，stop_reason 已在循环内赋值；
        # 若 while 条件自然耗尽（step_no == max_steps 且无 break），while-else 已兜底。
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass

    outcome.summary = (
        f"探索 {len(steps)} 步，提交 {len(outcome.cases)} 条用例"
        f"（登录：{outcome.login}，收敛：{outcome.stop_reason}）"
    )
    outcome.details = {
        "url": entry_url,
        "login": outcome.login,
        "stop_reason": outcome.stop_reason,
        "steps": steps,
        "cases_submitted": len(outcome.cases),
        "storage_state": storage_state,
    }
    return outcome


def run_explore_sync(entry_url: str, credentials: dict | None = None,
                     out_dir: str | None = None, llm_client: Any = None,
                     goal: str = "", progress_cb=None,
                     browser_factory: Any = None) -> ExploreOutcome:
    """同步包装（引擎后台线程直接调用；与 run_crawl_sync 同思路）。"""
    return run_explore(entry_url, credentials, out_dir, llm_client, goal,
                       progress_cb, browser_factory)
