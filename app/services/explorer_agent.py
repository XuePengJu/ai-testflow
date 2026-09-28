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
from app.services.decision_provider import (CONFIDENCE_HIGH, FAST_ACTIONS,  # noqa: E402
                                            FastDecider, llm_client_meta)
from app.services.view_fingerprint import (SIM_THRESHOLD as _FP_SIM_THRESHOLD,  # noqa: E402
                                           ViewFingerprint,
                                           fingerprint as _view_fingerprint,
                                           similarity as _view_similarity)

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


def _fingerprint_enabled() -> bool:
    """视图指纹判重开关（EXPLORE_FINGERPRINT，默认开 "1"；设 "0" 走旧逻辑）。

    SPA tab 切换 URL 不变，URL 判重失效导致重复漫游；开启后按
    规范化 URL + 激活态 + 元素集合（Jaccard ≥ 0.9）判同视图（M1）。
    """
    return os.getenv("EXPLORE_FINGERPRINT", "1") == "1"


def _guardrails_enabled() -> bool:
    """结构化护栏开关（EXPLORE_GUARDRAILS，默认开 "1"；设 "0" 走旧逻辑）。

    开启后导航/点击目标先过 M2 URL 白名单（explore=入口同域；提供 crawler
    pages.json 时取其 URL 集）；关闭后仅保留旧的域名锁定，不做点击目标预检。
    """
    return os.getenv("EXPLORE_GUARDRAILS", "1") == "1"


def _norm_url_key(url: str) -> tuple[str, str, str]:
    """URL 规范化为可比对的 (scheme, host, path)：忽略 fragment 与末尾斜杠。"""
    try:
        p = urlparse(url)
    except ValueError:
        return ("", "", "")
    return (p.scheme, p.netloc, (p.path or "/").rstrip("/"))


def build_url_whitelist(entry_url: str,
                        pages_json: str | Path | None = None) -> dict:
    """M2 结构化护栏：构建导航白名单（在 run_explore 初始化处调用）。

    - explore 任务：初始 URL 同域（不传 pages_json）
    - e2e 任务：crawler 产物 pages.json 存在时取其 URL 集（list[PageDesc] JSON，
      元素含 url 字段；兼容 {"pages": [...]} 包裹与纯字符串数组）

    返回：{"hosts": set[str], "url_keys": set[tuple] | None}——
    url_keys=None 表示「无 URL 集约束，仅按 hosts 同域判定」。
    解析失败时告警并回落同域白名单，绝不阻断任务。
    """
    hosts: set[str] = set()
    try:
        p = urlparse(entry_url)
        if p.scheme in ("http", "https") and p.netloc:
            hosts.add(p.netloc)
    except ValueError:
        pass
    url_keys: set[tuple[str, str, str]] | None = None
    if pages_json:
        try:
            data = json.loads(Path(pages_json).read_text(encoding="utf-8"))
            pages = data.get("pages") if isinstance(data, dict) else data
            keys: set[tuple[str, str, str]] = set()
            for pg in pages or []:
                u = str((pg.get("url") if isinstance(pg, dict) else pg) or "").strip()
                if not u:
                    continue
                up = urlparse(u)
                if up.scheme in ("http", "https") and up.netloc:
                    keys.add(_norm_url_key(u))
                    hosts.add(up.netloc)
            if keys:
                url_keys = keys
        except (OSError, ValueError) as e:
            logger.warning("护栏白名单构建：pages.json 解析失败（%s），回落同域白名单", e)
    return {"hosts": hosts, "url_keys": url_keys}


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
        try:
            # root 下必须加 --no-sandbox（复用 web_crawler 的启动参数计算）
            from app.services.web_crawler import _chromium_launch_args
            self._browser = self._pw.chromium.launch(headless=headless,
                                                     args=_chromium_launch_args())
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        except Exception:
            # 启动失败必须 stop：sync_playwright 在当前线程持有运行中的事件循环，
            # 不关会泄漏 running loop，导致同进程后续 async 测试/协程全部 RuntimeError
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            raise
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
        function isActive(el) {
            if (el.getAttribute('aria-selected') === 'true') return true;
            const cur = el.getAttribute('aria-current');
            if (cur === 'true' || cur === 'page') return true;
            const cls = (typeof el.className === 'string') ? el.className
                : (el.getAttribute('class') || '');
            return /(^|\\s)(active|selected|is-active|is-selected|ant-tabs-tab-active)(\\s|$)/i.test(cls);
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
            out.push({ref: 'e' + i, role: role(el), name: nm, css: cssPath(el),
                      active: isActive(el),
                      href: (el.tagName === 'A' && el.href) ? el.href : ''});
        }
        return out;
    }"""

    def snapshot(self) -> tuple[str, dict[str, dict]]:
        """返回 (可交互元素摘要文本, ref→元素信息注册表)。

    激活态元素（aria-selected / active class）在文本行追加「（选中）」标记，
    注册表 info 附带 active 布尔值——供视图指纹判别 SPA tab 切换后的视图；
    链接元素额外携带 href——供 M2 护栏对点击导航目标做预检。
    """
        js = self._SNAPSHOT_JS.replace("__MAX__", str(_SNAPSHOT_MAX_ELEMENTS))
        try:
            els = self._page.evaluate(js)
        except Exception as e:  # noqa: BLE001
            return f"(快照失败：{str(e)[:100]})", {}
        self._registry = {}
        lines = [f"URL：{self.url}", f"标题：{self.title or '(无标题)'}"]
        for el in els:
            ref = el["ref"]
            self._registry[ref] = {"role": el["role"], "name": el["name"], "css": el["css"],
                                   "active": bool(el.get("active")),
                                   "href": str(el.get("href") or "")}
            mark = "（选中）" if el.get("active") else ""
            lines.append(f"[{ref}] {el['role']} {el['name']}{mark}".rstrip())
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


# ============ 阶段 0 埋点：ReAct 耗时基线 ============
#
# 分层决策改造前先量化瓶颈：决策（LLM 调用）到底占探索总耗时的几成？
# 只加观测，不改 ReAct 分支逻辑。数据两处落地：
#   1) outcome.details["metrics"] —— 随 StepLog 走，前端 ExploreTimeline 可读
#   2) out_dir/explore/metrics.json —— 离线分析、阶段 1/2 前后对照
# 关键指标是 llm_pct：占比高 → 决策层改造有收益；占比低 → 瓶颈在浏览器动作，
# 该去优化等待策略而不是换模型。

@dataclass
class ExploreMetrics:
    step_ms: list = field(default_factory=list)       # 每步墙钟耗时
    snapshot_ms: list = field(default_factory=list)   # browser.snapshot() 耗时
    llm_ms: list = field(default_factory=list)        # 决策模型调用耗时
    llm_in: list = field(default_factory=list)        # 决策输入字符数
    llm_out: list = field(default_factory=list)       # 决策输出字符数
    actions: dict = field(default_factory=dict)       # 动作 → 次数
    parse_errors: int = 0                             # 决策 JSON 解析失败次数
    danger_blocked: int = 0                           # 危险操作拦截次数
    repeats: int = 0                                  # 重复动作次数
    fast_ms: list = field(default_factory=list)       # 快速判断（分层模式）耗时
    fast_in: list = field(default_factory=list)       # 快速判断输入字符数
    fast_out: list = field(default_factory=list)      # 快速判断输出字符数
    fast_hits: int = 0                                # 快速判断直接执行次数
    escalations: int = 0                              # 快速判断升级主 LLM 次数
    repeat_blocked: int = 0                           # 震荡指纹硬拒次数
    fast_skipped: int = 0                             # 按路由预测跳过快判的次数

    def note_action(self, action: str) -> None:
        self.actions[action] = self.actions.get(action, 0) + 1

    @staticmethod
    def _stat(xs: list) -> dict:
        if not xs:
            return {"n": 0, "sum": 0, "avg": 0, "p50": 0, "p95": 0}
        s = sorted(int(x) for x in xs)
        n = len(s)

        def q(p: float) -> int:
            i = min(n - 1, max(0, int(round((n - 1) * p))))
            return s[i]

        return {"n": n, "sum": int(sum(s)), "avg": int(sum(s) / n),
                "p50": q(0.5), "p95": q(0.95)}

    def summary(self) -> dict:
        total = int(sum(self.step_ms))
        llm = int(sum(self.llm_ms))
        snap = int(sum(self.snapshot_ms))
        fast = int(sum(self.fast_ms))
        browser = max(0, total - llm - snap - fast)

        def pct(x: int) -> float:
            return round(x / total * 100, 1) if total else 0.0

        return {
            "steps": len(self.step_ms),
            "total_ms": total,
            "llm_total_ms": llm,
            "llm_pct": pct(llm),
            "fast_pct": pct(fast),
            "snapshot_pct": pct(snap),
            "browser_pct": pct(browser),
            "step_ms": self._stat(self.step_ms),
            "snapshot_ms": self._stat(self.snapshot_ms),
            "llm_ms": self._stat(self.llm_ms),
            "llm_in_chars": self._stat(self.llm_in),
            "llm_out_chars": self._stat(self.llm_out),
            "actions": dict(sorted(self.actions.items(), key=lambda kv: -kv[1])),
            "parse_errors": self.parse_errors,
            "danger_blocked": self.danger_blocked,
            "repeats": self.repeats,
            "fast_hits": self.fast_hits,
            "escalations": self.escalations,
            "repeat_blocked": self.repeat_blocked,
            "fast_skipped": self.fast_skipped,
            "fast_ms": self._stat(self.fast_ms),
            "fast_in_chars": self._stat(self.fast_in),
            "fast_out_chars": self._stat(self.fast_out),
        }


def _write_metrics_file(out_dir: str | None, metrics: dict) -> str:
    """埋点落盘（离线分析用）；失败只告警，不影响主流程。"""
    if not out_dir:
        return ""
    try:
        p = Path(out_dir) / "explore" / "metrics.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(metrics, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return str(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("埋点落盘失败：%s", e)
        return ""


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


# ============ M3 计划先行：探索计划生成 / 校验 / 消费 ============

_PLAN_SYSTEM_PROMPT = """你是资深测试分析师。根据被测系统的需求描述与首页快照，规划要探索的业务流。

只输出一个 JSON 对象（不要 markdown 围栏、不要解释文字）：
{"flows": [{"name": "业务流名称", "steps": ["操作步骤描述", "…"]}], "reason": "一句话规划理由"}

要求：
- 产出 3~5 条业务流，覆盖系统核心功能（需求描述中点名的功能优先）
- steps 为人可读的操作步骤描述数组，每条 2~6 步，不写元素定位等技术细节
- 业务流名称不超过 20 字"""


def _plan_seed_snapshot(entry_url: str, browser_factory: Any = None) -> str:
    """尽力而为获取种子页快照：作计划生成的 LLM 输入；任何失败返回空串
    （浏览器不可用/页面打不开都不阻断计划生成，退化为仅按需求文本规划）。"""
    try:
        browser = (browser_factory or BrowserSession)()
    except Exception:  # noqa: BLE001
        return ""
    try:
        ok, _ = browser.goto(entry_url)
        if not ok:
            return ""
        text, _registry = browser.snapshot()
        return text[:4000]
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass


def _fallback_plan(goal: str) -> list[dict]:
    """计划生成失败时的兜底：单条自由探索流（不阻塞确认流程，用户可编辑后确认）。"""
    return [{
        "name": (goal or "自由探索").strip()[:20] or "自由探索",
        "steps": ["浏览首页了解主要功能入口", "逐个功能流探索并提交用例"],
    }]


def normalize_plan_flows(flows: Any) -> list[dict]:
    """任意 flows 输入 → 合法计划结构（非法条目丢弃，字段截断兜底）。"""
    out: list[dict] = []
    if not isinstance(flows, list):
        return out
    for f in flows:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "").strip()
        steps = [str(s).strip() for s in (f.get("steps") or []) if str(s).strip()]
        if not name or not steps:
            continue
        out.append({"name": name[:60], "steps": steps[:10]})
    return out[:8]


def validate_plan_payload(payload: Any) -> list[dict]:
    """校验 confirm 提交的计划 JSON 结构；非法抛 ValueError（API 层转 400）。"""
    if not isinstance(payload, dict):
        raise ValueError("计划必须是 JSON 对象（含 flows 数组）")
    flows = normalize_plan_flows(payload.get("flows"))
    if not flows:
        raise ValueError("flows 必须是非空数组，且每条业务流都需含 name 与非空 steps 数组")
    return flows


def load_explore_plan(out_dir: str | None) -> dict | None:
    """读 out_dir/plan.json；文件缺失 / 解析失败 → None（回落自由探索）。"""
    if not out_dir:
        return None
    try:
        p = Path(out_dir) / "plan.json"
        if not p.is_file():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError) as e:
        logger.warning("plan.json 读取失败（%s），回落自由探索", e)
        return None


def save_explore_plan(out_dir: str | None, plan: dict) -> str:
    """计划整体覆盖写 out_dir/plan.json；失败告警返回空串（不抛异常阻断任务）。"""
    if not out_dir:
        return ""
    try:
        p = Path(out_dir) / "plan.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)
    except OSError as e:
        logger.warning("plan.json 写入失败：%s", e)
        return ""


def generate_explore_plan(entry_url: str, goal: str = "",
                          llm_client: Any = None, out_dir: str | None = None,
                          task_id: str = "", browser_factory: Any = None,
                          progress_cb=None) -> dict:
    """计划先行首步骤：LLM 产出 3~5 条业务流计划 → 落盘 out_dir/plan.json。

    LLM 输入 = 用户需求（goal）+ 种子页快照（尽力获取，失败忽略）。
    模型不可用 / 输出非法 → 兜底单条自由探索流（仍进确认流程，不阻塞任务）。

    返回 {"flows", "summary", "details"}（引擎直接作 StepLog 的 summary/details）。
    """
    def _progress(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:  # noqa: BLE001
                pass

    snapshot_text = ""
    if entry_url:
        _progress("打开种子页获取页面快照…")
        snapshot_text = _plan_seed_snapshot(entry_url, browser_factory)
    _progress("正在生成探索计划…")

    flows: list[dict] = []
    reason = ""
    llm_ok = False
    if llm_client is not None:
        user = (f"【用户需求】\n{goal or '（无需求描述，请按页面快照规划）'}\n\n"
                f"【种子页快照】\n{snapshot_text or '（无可用快照）'}")
        try:
            raw = llm_client.chat(
                [{"role": "system", "content": _PLAN_SYSTEM_PROMPT},
                 {"role": "user", "content": user[:12000]}],
                temperature=0.3, max_tokens=1500)
            obj = _strip_fences(raw or "")
            start, end = obj.find("{"), obj.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(obj[start:end + 1])
                flows = normalize_plan_flows(data.get("flows"))
                reason = str(data.get("reason") or "").strip()
                llm_ok = bool(flows)
        except Exception as e:  # noqa: BLE001  LLM 网络/解析失败 → 兜底计划
            logger.info("探索计划生成失败（%s），改用兜底计划", str(e)[:120])
    fallback = not llm_ok
    if not flows:
        flows = _fallback_plan(goal)

    from app.core.utils import utcnow
    plan = {
        "task_id": task_id,
        "entry_url": entry_url,
        "goal": goal,
        "confirmed": False,
        "created_at": utcnow().isoformat(),
        "seed_snapshot": bool(snapshot_text),
        "flows": flows,
    }
    path = save_explore_plan(out_dir, plan)
    summary = f"探索计划已生成：{len(flows)} 条业务流，等待确认后开始探索"
    if fallback:
        summary += "（模型生成失败，已用兜底计划，可编辑后确认）"
    details = {
        "url": entry_url,
        "goal": goal,
        "flows": flows,
        "reason": reason,
        "seed_snapshot": bool(snapshot_text),
        "fallback": fallback,
        "plan_file": path,
    }
    return {"flows": flows, "summary": summary, "details": details}


def _build_plan_context(plan: dict | None) -> str:
    """已确认计划 → 决策 prompt 注入文本（无有效计划返回空串）。"""
    if not plan:
        return ""
    flows = normalize_plan_flows(plan.get("flows"))
    if not flows:
        return ""
    lines = ["【用户已确认的探索计划（按此顺序逐流探索）】"]
    for i, f in enumerate(flows, 1):
        lines.append(f"业务流{i}：{f['name']}")
        for j, s in enumerate(f["steps"], 1):
            lines.append(f"  {j}. {s}")
    lines.append("规则：按业务流顺序探索，每探索完一条业务流立即 submit_cases 提交该流的用例；"
                 "全部业务流完成后 submit_cases(done=true) 结束。")
    return "\n".join(lines)


# ============ M1 视图指纹判重：已见视图上下文注入 ============

# 视图切换入口：点击后视图变化但 URL 往往不变（SPA tab / 菜单项）
_TRIGGER_ROLES = ("tab", "menuitem")
# 注入 prompt 的已见视图条数上限（防长会话把上下文打爆）
_FP_CONTEXT_MAX_VIEWS = 8


def _build_fp_context(visited: list[dict], current: dict | None) -> str:
    """已见视图清单 + 未触发入口 → 决策 prompt 注入文本（无视图时返回空串）。

    visited 元素契约：{"fp": ViewFingerprint, "tab_triggers": set[str],
    "clicked": set[str]}。「探索完」= 该视图全部 tab 触发器点过一遍。
    """
    if not visited:
        return ""
    lines = ["【已见视图清单（勿重复漫游）】"]
    for i, v in enumerate(visited[-_FP_CONTEXT_MAX_VIEWS:], 1):
        fp = v["fp"]
        pending = sorted(v["tab_triggers"] - v["clicked"])
        explored = bool(v["tab_triggers"]) and not pending
        line = (f"视图{i} url={fp.url} 激活={fp.active or '-'} "
                f"元素{len(fp.elements)}个 {'已探索完' if explored else '未探索完'}")
        if pending and v is current:
            line += f"；未触发入口：{'、'.join(pending[:8])}"
        lines.append(line)
    if current is not None:
        pending = sorted(current["tab_triggers"] - current["clicked"])
        if pending:
            lines.append(f"当前视图未触发的 tab/菜单入口：{'、'.join(pending[:8])}（优先点击它们）")
    lines.append("规则：禁止重复漫游已见视图；未探索完的视图先把它的 tab/菜单入口点一遍；"
                 "入口点完或视图已探索完 → 离开该视图，或 submit_cases 提交用例。")
    return "\n".join(lines)


# ============ 决策提示词 ============

_SYSTEM_PROMPT = """你是探索式测试 Agent（ReAct 模式），目标站点：{url}。任务：{goal}

每轮你必须只输出一个 JSON 对象（不要 markdown 围栏、不要任何解释文字）：
{{"reason": "一句话理由", "tool": "工具名", "args": {{...}}, "next": "fast"}}

next 字段：预测你执行完本步后的下一个动作类型——若仍是点击/导航/后退/截图等
纯浏览动作，输出 "fast"（快速决策器接管以提速）；若需要填表、编写用例或复杂
推理，输出 "main"。

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
                             step_no: int = 0, max_steps: int = 0,
                             fp_context: str = "",
                             plan_context: str = "") -> list[dict]:
    """组装决策上下文：system + 截断的 history + 当前观察（+ 计划 + 视图判重上下文）。"""
    recent = history[-12:]  # 截断防 token 爆炸
    hist_text = "\n".join(recent) if recent else "（暂无历史动作）"
    user = (f"【历史动作与结果】\n{hist_text}\n\n【当前页面观察】\n{obs}\n\n"
            + (f"{plan_context}\n\n" if plan_context else "")
            + (f"{fp_context}\n\n" if fp_context else "")
            + f"【进度】当前第 {step_no}/{max_steps} 步。"
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
                browser_factory: Any = None,
                plan: dict | None = None) -> ExploreOutcome:
    """探索式测试主入口：ReAct 循环产出用例。

    入参：
        entry_url       ：被测系统入口 URL（域名锁定基准）
        credentials     ：可选 {"username","password"}，有账密首步先登录
        out_dir         ：任务输出目录（截图落 out_dir/explore/step-NNN.png；
                          含 crawler pages.json 时 M2 白名单取其 URL 集）
        llm_client      ：有 .chat(messages, **kw) 的客户端；None → 不启动浏览器直接收敛
        goal            ：探索目标描述（任务名）
        progress_cb     ：可选回调(str)，每步更新 StepLog.progress
        browser_factory ：可注入浏览器工厂（测试用 FakeBrowser）；缺省 PlaywrightBrowser
        plan            ：M3 计划先行——用户已确认的探索计划（plan.json 内容）；
                          非空时注入决策上下文，引导按业务流逐流探索

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
    metrics = ExploreMetrics()
    seen_fp: dict[str, int] = {}

    # ---- M2 结构化护栏：白名单在初始化处构建 ----
    # explore 任务 = 入口同域白名单；out_dir 下若存在 crawler 的 pages.json
    # （e2e 链路产物），改取其 URL 集。EXPLORE_GUARDRAILS=0 → 整体回落旧逻辑。
    guardrails = _guardrails_enabled()
    whitelist: dict = {"hosts": {entry_host} if entry_host else set(), "url_keys": None}
    if guardrails:
        pages_json = Path(out_dir) / "pages.json" if out_dir else None
        whitelist = build_url_whitelist(
            entry_url, pages_json if pages_json and pages_json.is_file() else None)

    # ---- M3 计划先行：已确认计划 → 决策上下文注入文本 ----
    plan_ctx = _build_plan_context(plan)
    plan_flows = normalize_plan_flows(plan.get("flows")) if plan else []

    # 视图指纹判重（M1，EXPLORE_FINGERPRINT=1 默认开；纯内存，单次任务内）：
    # visited 每项 {"fp": ViewFingerprint, "tab_triggers": set[str], "clicked": set[str]}
    fp_enabled = _fingerprint_enabled()
    visited: list[dict] = []
    current_view: dict | None = None
    no_new_streak = 0     # 同一视图连续无新元素的步数（循环检测用）
    # 当前步是否进入新视图（M3 前端时间线「新视图/已见」徽章数据源）：
    # fp 关闭时保持 None（步骤记录不带 is_new_view 字段）
    new_view_flag: bool | None = None

    # 分层决策开关（EXPLORE_LAYERED=1）：每步先走快速判断，高置信直接执行，
    # 低置信 / 需要生成（fill、submit_cases）/ 解析失败 → 升级主 LLM。默认关闭。
    fast: FastDecider | None = None
    if os.getenv("EXPLORE_LAYERED", "0") == "1":
        fast = FastDecider(llm_client)
    # 路由提示：上一步决策对"下一步动作类型"的预测（fast=纯浏览 / main=需生成）。
    # 首步固定 main：登录判断与首屏规划交给主模型。避免"先快判失败再升级"
    # 的双重调用浪费（首次对照实验 10/12 步升级 → 每步白花一次快判调用）。
    route_hint = "main"

    def _allow_url(url: str) -> tuple[bool, str]:
        """URL 护栏裁决：先做协议/入口域基础校验，再按 M2 白名单判定。

        EXPLORE_GUARDRAILS=0（旧逻辑）：仅域名锁定；开启后白名单统一裁决——
        无 URL 集时同域放行（行为与旧域名锁一致），有 URL 集时仅放行集合内页面。
        """
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
        if not entry_host:
            return False, f"已拒绝：入口域缺失，不允许导航到 {url}"
        if not guardrails:
            # 旧逻辑：仅域名锁定
            if p.netloc != entry_host:
                return False, f"已拒绝：越域导航 {url}（域名锁定，仅允许 {entry_host}）"
            return True, "ok"
        # M2 白名单裁决
        if p.netloc not in whitelist["hosts"]:
            return False, (f"已拒绝：越界被护栏拦截，越域导航 {url}"
                           f"（白名单仅允许 {entry_host} 域内页面）")
        if whitelist["url_keys"] is not None:
            if _norm_url_key(url) not in whitelist["url_keys"]:
                return False, (f"已拒绝：越界被护栏拦截，{url} 不在允许页面集内"
                               "（仅允许 crawler 抓取记录过的页面）")
        return True, "ok"

    def _add_step(url: str, action: str, args: Any, reason: str,
                  ok: bool, msg: str, shot_rel: str = "") -> None:
        """记录一步 + 更新进度。"""
        rec = {"n": len(steps) + 1, "url": url, "action": action,
               "args": args, "reason": reason, "result": ("ok" if ok else f"拒绝/失败：{msg}"),
               "screenshot": shot_rel}
        if new_view_flag is not None:
            # M3 前端时间线徽章：True=新视图 / False=已见视图（fp 关闭时不记）
            rec["is_new_view"] = bool(new_view_flag)
        steps.append(rec)
        outcome.steps = steps
        metrics.note_action(action)
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
            t_step = time.monotonic()
            t_snap = time.monotonic()
            obs, registry = browser.snapshot()
            metrics.snapshot_ms.append((time.monotonic() - t_snap) * 1000)
            if browser.url not in outcome.page_obs:
                outcome.page_obs[browser.url] = obs

            # ---- M1 视图指纹判重：快照后先查重（Jaccard ≥ 0.9 判同视图）----
            fp_context = ""
            new_view_flag = None
            if fp_enabled:
                fp = _view_fingerprint(browser.url, obs)
                matched: dict | None = None
                best_sim = 0.0
                for v in visited:
                    sim = _view_similarity(fp, v["fp"])
                    if sim > best_sim:
                        best_sim, matched = sim, v
                if matched is not None and best_sim >= _FP_SIM_THRESHOLD:
                    current_view = matched
                    new_view_flag = False
                    old_elems = set(matched["fp"].elements)
                    merged = old_elems | set(fp.elements)
                    if len(merged) > len(old_elems):
                        # 回访视图出现新元素（懒加载/展开）：并入指纹并刷新计数
                        matched["fp"] = ViewFingerprint(
                            url=matched["fp"].url,
                            active=matched["fp"].active or fp.active,
                            elements=tuple(sorted(merged)))
                        no_new_streak = 0
                    else:
                        no_new_streak += 1
                    # 回访时补录新出现的 tab/菜单触发器（懒加载菜单可能后出现）
                    for info in registry.values():
                        role = str(info.get("role") or "")
                        if role in _TRIGGER_ROLES and info.get("name"):
                            matched["tab_triggers"].add(str(info["name"]))
                    if no_new_streak >= 3:
                        # 循环检测：同视图连续 3 步无新元素 → 强制换目标
                        no_new_streak = 0
                        history.append(
                            "【系统警告】已连续 3 步处于同一视图且无新元素（可能在绕圈）。"
                            "强制换目标：点击当前视图尚未点过的 tab/菜单入口，"
                            "或 browser_navigate 前往其他页面；"
                            "功能流已探索充分则立即 submit_cases(done=true) 结束。")
                        logger.info("视图循环检测：同视图连续 3 步无新元素 url=%s",
                                    matched["fp"].url)
                else:
                    # 新视图：登记指纹 + 收集 tab/菜单触发器
                    current_view = {"fp": fp, "tab_triggers": set(),
                                    "clicked": set()}
                    visited.append(current_view)
                    no_new_streak = 0
                    new_view_flag = True
                    for info in registry.values():
                        role = str(info.get("role") or "")
                        if role in _TRIGGER_ROLES and info.get("name"):
                            current_view["tab_triggers"].add(str(info["name"]))
                fp_context = _build_fp_context(visited, current_view)

            # ---- 分层决策：按上一步的 next 预测路由（EXPLORE_LAYERED=1 时）----
            plan: dict | None = None
            if fast is not None and route_hint == "fast":
                fd, fmeta = fast.decide(goal, entry_host or entry_scheme,
                                        history, obs, step_no, max_steps)
                metrics.fast_ms.append(fmeta["ms"])
                metrics.fast_in.append(fmeta["in"])
                metrics.fast_out.append(fmeta["out"])
                est_tokens += (fmeta["in"] + fmeta["out"]) // 2
                if fd.action in FAST_ACTIONS and fd.confidence >= CONFIDENCE_HIGH:
                    plan = {"reason": fd.reason or "快速判断", "tool": fd.action,
                            "args": fd.args}
                    metrics.fast_hits += 1
                    route_hint = fd.next
                else:
                    # 预测失误（这步其实需要生成/低置信）→ 升级主 LLM，
                    # 且下一步路由改由主 LLM 的 next 决定
                    metrics.escalations += 1
                    route_hint = "main"
            elif fast is not None:
                metrics.fast_skipped += 1

            if plan is None:
                # 升级主 LLM：低置信 / 需要生成（fill、submit_cases）/ 快判失败
                messages = _build_decision_messages(goal, entry_url,
                                                    entry_host or entry_scheme, history, obs,
                                                    step_no, max_steps,
                                                    fp_context=fp_context,
                                                    plan_context=plan_ctx)
                try:
                    t_llm = time.monotonic()
                    raw = llm_client.chat(messages, temperature=0.2, max_tokens=2048)
                    llm_ms = (time.monotonic() - t_llm) * 1000
                    metrics.llm_ms.append(llm_ms)
                    metrics.llm_in.append(sum(len(str(m["content"])) for m in messages))
                    metrics.llm_out.append(len(raw or ""))
                    # LLM 调用观测日志（M1 补点）：provider / model / 耗时，不记 prompt 全文
                    logger.info("决策 LLM 调用成功 %s ms=%d out=%d",
                                llm_client_meta(llm_client), int(llm_ms), len(raw or ""))
                except Exception as e:  # noqa: BLE001  LLM 网络失败 → 收敛不崩
                    logger.info("决策 LLM 调用失败 %s ms=%d err=%s",
                                llm_client_meta(llm_client),
                                int((time.monotonic() - t_llm) * 1000), str(e)[:120])
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
                    metrics.parse_errors += 1
                    metrics.step_ms.append((time.monotonic() - t_step) * 1000)
                    continue
                if fast is not None:
                    nh = str(plan.get("next") or "").strip().lower()
                    route_hint = nh if nh in ("fast", "main") else "main"

            action = str(plan.get("tool") or "").strip()
            args = plan.get("args") if isinstance(plan.get("args"), dict) else {}
            reason = str(plan.get("reason") or "").strip()
            done_flag = bool(plan.get("done") or args.get("done"))

            # 防重复护栏：连续 3 次完全相同的动作 → 注入收敛警告（模型卡死时兜底）
            action_key = f"{action}|{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            repeat_count = repeat_count + 1 if action_key == last_action_key else 1
            last_action_key = action_key
            if repeat_count == 3:
                metrics.repeats += 1
                history.append("【系统警告】已连续 3 次执行完全相同的动作。"
                               "请改变探索目标；若功能流已探索充分，立即 submit_cases 提交用例并结束。")

            # 震荡检测（阶段 0 发现的缺陷修复）：navigate→back→navigate 这类
            # A→B 交替震荡，上面的连续重复护栏抓不到。按 (url, action, args)
            # 指纹跨步去重：第 2 次注入警告，第 3 次起硬拒（submit_cases 豁免）。
            if action != "submit_cases":
                fp = (f"{browser.url}|{action}|"
                      f"{json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)}")
                seen_fp[fp] = seen_fp.get(fp, 0) + 1
                fp_n = seen_fp[fp]
                if fp_n == 2:
                    history.append("【系统警告】该动作在当前页面已执行过且结果相同，"
                                   "请勿重复；请换一个目标或路径。")
                elif fp_n >= 3:
                    metrics.repeat_blocked += 1
                    _add_step(browser.url, action, args, reason, False,
                              f"震荡拦截（同页面同动作已执行 {fp_n - 1} 次无新进展）")
                    history.append(f"[{action}] → 已拦截：同页面重复 {fp_n - 1} 次无新进展，"
                                   "禁止再执行相同动作；请立即 submit_cases 或探索其他区域")
                    metrics.step_ms.append((time.monotonic() - t_step) * 1000)
                    continue

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
                        metrics.danger_blocked += 1
                        deny = f"已拒绝：命中危险操作黑名单（「{kw}」），一期禁止执行该点击"
                        _add_step(browser.url, action, args, reason, False, deny)
                        history.append(f"[{action}] {ref}({info.get('name')}) → {deny}")
                    else:
                        # M2 结构化护栏：链接元素的导航目标先过白名单（与 browser_navigate 同规则）
                        href = str(info.get("href") or "")
                        href_blocked = ""
                        if guardrails and href:
                            href_ok, href_deny = _allow_url(href)
                            if not href_ok:
                                href_blocked = href_deny
                        if href_blocked:
                            _add_step(browser.url, action, args, reason, False, href_blocked)
                            history.append(
                                f"[{action}] {ref}({info.get('name')}) → {href_blocked}")
                        else:
                            ok, msg = browser.click(ref)
                            shot = _shot(browser)
                            _add_step(browser.url, action, args, reason, ok, msg, shot)
                            history.append(f"[{action}] {ref}({info.get('name')}) → {'ok' if ok else msg}")
                            # M1：点击 tab/菜单入口成功 → 记入当前视图已触发集合
                            if fp_enabled and current_view is not None and ok \
                                    and str(info.get("role") or "") in _TRIGGER_ROLES:
                                current_view["clicked"].add(str(info.get("name") or ""))

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
                    metrics.step_ms.append((time.monotonic() - t_step) * 1000)
                    outcome.stop_reason = "done"
                    break
                history.append("（done=false：可继续探索其他功能流，结束时请 submit_cases 且 done=true）")

            else:
                deny = f"未知工具 {action}，可用工具见系统提示"
                _add_step(browser.url, action, args, reason, False, deny)
                history.append(f"[{action}] → {deny}")

            metrics.step_ms.append((time.monotonic() - t_step) * 1000)

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
    m = metrics.summary()
    outcome.details = {
        "url": entry_url,
        "login": outcome.login,
        "stop_reason": outcome.stop_reason,
        "steps": steps,
        "cases_submitted": len(outcome.cases),
        "storage_state": storage_state,
        "metrics": m,
        "metrics_file": _write_metrics_file(out_dir, m),
        "views_seen": len(visited),
        # M3 计划先行：本次探索消费的计划（未启用计划时为空数组）
        "plan_flows": [f["name"] for f in plan_flows],
    }
    return outcome


def run_explore_sync(entry_url: str, credentials: dict | None = None,
                     out_dir: str | None = None, llm_client: Any = None,
                     goal: str = "", progress_cb=None,
                     browser_factory: Any = None) -> ExploreOutcome:
    """同步包装（引擎后台线程直接调用；与 run_crawl_sync 同思路）。"""
    return run_explore(entry_url, credentials, out_dir, llm_client, goal,
                       progress_cb, browser_factory)
